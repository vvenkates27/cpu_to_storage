#include <torch/extension.h>

#include <iostream>
#include <string>
#include <vector>
#include <future>
#include <thread>
#include <cstdlib>
#include <memory>
#include <mutex>
#include <filesystem>
#include <fcntl.h>
#include <unistd.h>
#include <cstring>
#include <cerrno>

namespace fs = std::filesystem;

#include "simple_thread_pool.hpp"

// Global O_DIRECT flag (can be toggled at runtime)
static bool g_use_o_direct = true;

// When true, write directly to the destination file (no .tmp + rename).
static bool g_no_rename = false;

// Read block_size bytes from path into buffer_ptr using pread().
static bool pread_file(const std::string& path, uint8_t* buffer_ptr, size_t block_size) {
#ifdef O_DIRECT
  int fd = -1;
  if (g_use_o_direct) {
    fd = open(path.c_str(), O_RDONLY | O_DIRECT);
    if (fd < 0 && errno == EINVAL)
      fd = open(path.c_str(), O_RDONLY);
  } else {
    fd = open(path.c_str(), O_RDONLY);
  }
#else
  int fd = open(path.c_str(), O_RDONLY);
#endif
  if (fd < 0) {
    std::cerr << "[ERROR] Failed to open file: " << path
              << " - " << std::strerror(errno) << "\n";
    return false;
  }
  size_t total_read = 0;
  while (total_read < block_size) {
    ssize_t n = pread(fd, buffer_ptr + total_read, block_size - total_read,
                      static_cast<off_t>(total_read));
    if (n < 0) {
      if (errno == EINTR) continue;
      std::cerr << "[ERROR] pread failed: " << path
                << " - " << std::strerror(errno) << "\n";
      close(fd);
      return false;
    }
    if (n == 0) {
      std::cerr << "[ERROR] Unexpected EOF: " << path
                << " (read " << total_read << "/" << block_size << " bytes)\n";
      close(fd);
      return false;
    }
    total_read += n;
  }
  close(fd);
  return true;
}

void set_o_direct(bool enabled) {
  g_use_o_direct = enabled;
}

bool get_o_direct() {
  return g_use_o_direct;
}

void set_no_rename(bool enabled) {
  g_no_rename = enabled;
}

bool get_no_rename() {
  return g_no_rename;
}

// Global thread pool configuration
static std::mutex g_pool_mutex;
static std::unique_ptr<SimpleThreadPool> g_thread_pool;
static size_t g_thread_count = 0;

static size_t get_default_thread_count() {
  const char* env_threads = std::getenv("IO_THREAD_COUNT");
  if (env_threads) {
    try {
      size_t count = std::stoul(env_threads);
      if (count > 0) return count;
    } catch (...) {}
  }
  size_t hw_threads = std::thread::hardware_concurrency();
  return hw_threads > 0 ? hw_threads : 8;
}

static SimpleThreadPool& get_thread_pool() {
  std::lock_guard<std::mutex> lock(g_pool_mutex);
  if (!g_thread_pool) {
    if (g_thread_count == 0) g_thread_count = get_default_thread_count();
    g_thread_pool = std::make_unique<SimpleThreadPool>(g_thread_count);
  }
  return *g_thread_pool;
}

void set_thread_count(size_t num_threads) {
  if (num_threads == 0)
    throw std::runtime_error("Thread count must be greater than 0");
  std::lock_guard<std::mutex> lock(g_pool_mutex);
  g_thread_count = num_threads;
  g_thread_pool.reset();
  g_thread_pool = std::make_unique<SimpleThreadPool>(g_thread_count);
  std::cerr << "[INFO] Thread pool recreated with " << num_threads << " threads\n";
}

size_t get_io_thread_count() {
  std::lock_guard<std::mutex> lock(g_pool_mutex);
  if (g_thread_count == 0) g_thread_count = get_default_thread_count();
  return g_thread_count;
}

// cpp_read_blocks: each worker opens its own FD and uses pread().
// For reads, files already exist so there is no O_CREAT directory-inode
// contention. Opening FDs inside worker threads lets the opens happen in
// parallel, which is faster than pre-opening them sequentially in the main thread.
bool cpp_read_blocks(torch::Tensor buffer,
                     int64_t block_size,
                     std::vector<int64_t> block_indices,
                     std::vector<std::string> source_files) {
  if (!buffer.is_cpu())
    throw std::runtime_error("Buffer must be on CPU");
  if (!buffer.is_contiguous())
    throw std::runtime_error("Buffer must be contiguous");
  if (block_indices.size() != source_files.size())
    throw std::runtime_error("block_indices and source_files must have the same size");

  int64_t buffer_size = buffer.numel() * buffer.element_size();
  for (size_t i = 0; i < block_indices.size(); i++) {
    int64_t block_offset = block_indices[i] * block_size;
    if (block_offset + block_size > buffer_size)
      throw std::runtime_error("Block index " + std::to_string(block_indices[i]) +
                               " out of bounds for buffer size " +
                               std::to_string(buffer_size));
  }

  py::gil_scoped_release release;

  uint8_t* data_ptr = static_cast<uint8_t*>(buffer.data_ptr());
  SimpleThreadPool& pool = get_thread_pool();

  std::vector<std::future<bool>> futures;
  futures.reserve(block_indices.size());

  for (size_t i = 0; i < block_indices.size(); i++) {
    int64_t block_offset = block_indices[i] * block_size;
    std::string source_file = std::move(source_files[i]);

    futures.push_back(pool.enqueue([data_ptr, block_offset, block_size, source_file]() -> bool {
      return pread_file(source_file, data_ptr + block_offset, block_size);
    }));
  }

  bool all_success = true;
  for (auto& fut : futures) {
    if (!fut.get()) all_success = false;
  }
  if (!all_success) std::cerr << "[WARN] Some read operations failed\n";
  return all_success;
}

// cpp_write_blocks: opens all temp-file FDs sequentially in the main thread,
// then dispatches workers to do pure pwrite + close + rename.
// For writes, each new file requires O_CREAT which contends on the directory
// inode. Opening them one-by-one in the main thread before dispatch serializes
// that metadata work and avoids concurrent contention.
bool cpp_write_blocks(torch::Tensor buffer,
                      int64_t block_size,
                      std::vector<int64_t> block_indices,
                      std::vector<std::string> dest_files) {
  if (!buffer.is_cpu())
    throw std::runtime_error("Buffer must be on CPU");
  if (!buffer.is_contiguous())
    throw std::runtime_error("Buffer must be contiguous");
  if (block_indices.size() != dest_files.size())
    throw std::runtime_error("block_indices and dest_files must have the same size");

  int64_t buffer_size = buffer.numel() * buffer.element_size();
  for (size_t i = 0; i < block_indices.size(); i++) {
    int64_t block_offset = block_indices[i] * block_size;
    if (block_offset + block_size > buffer_size)
      throw std::runtime_error("Block index " + std::to_string(block_indices[i]) +
                               " out of bounds for buffer size " +
                               std::to_string(buffer_size));
  }

  py::gil_scoped_release release;

  uint8_t* buffer_ptr = static_cast<uint8_t*>(buffer.data_ptr());
  SimpleThreadPool& pool = get_thread_pool();
  size_t n = block_indices.size();

  // Open all file FDs sequentially before dispatching any task.
  // When g_no_rename is true we open the final destination directly;
  // otherwise we open a .tmp sidecar and rename after the write completes.
  std::vector<int> fds(n, -1);
  std::vector<std::string> open_paths(n);  // path actually opened (tmp or dest)

  for (size_t i = 0; i < n; i++) {
    fs::path parent_dir = fs::path(dest_files[i]).parent_path();
    if (!parent_dir.empty()) {
      std::error_code ec;
      fs::create_directories(parent_dir, ec);
      if (ec) {
        std::cerr << "[ERROR] Failed to create directories: " << ec.message() << "\n";
        for (size_t j = 0; j < i; j++) if (fds[j] >= 0) close(fds[j]);
        return false;
      }
    }
    open_paths[i] = g_no_rename ? dest_files[i] : dest_files[i] + ".tmp";
#ifdef O_DIRECT
    if (g_use_o_direct) {
      fds[i] = open(open_paths[i].c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_DIRECT, 0644);
      if (fds[i] < 0 && errno == EINVAL)
        fds[i] = open(open_paths[i].c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
    } else {
      fds[i] = open(open_paths[i].c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
    }
#else
    fds[i] = open(open_paths[i].c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
#endif
    if (fds[i] < 0) {
      std::cerr << "[ERROR] Failed to open file: " << open_paths[i]
                << " - " << std::strerror(errno) << "\n";
      for (size_t j = 0; j < i; j++) if (fds[j] >= 0) close(fds[j]);
      return false;
    }
  }

  bool no_rename = g_no_rename;  // capture before threads start
  std::vector<std::future<bool>> futures;
  futures.reserve(n);

  for (size_t i = 0; i < n; i++) {
    int64_t block_offset = block_indices[i] * block_size;
    int fd = fds[i];
    std::string open_path = std::move(open_paths[i]);
    std::string dest_file = std::move(dest_files[i]);

    futures.push_back(pool.enqueue([buffer_ptr, block_offset, block_size,
                                    fd, open_path, dest_file, no_rename]() -> bool {
      size_t total_written = 0;
      while (total_written < static_cast<size_t>(block_size)) {
        ssize_t written = pwrite(fd,
                                 buffer_ptr + block_offset + total_written,
                                 block_size - total_written,
                                 static_cast<off_t>(total_written));
        if (written < 0) {
          if (errno == EINTR) continue;
          std::cerr << "[ERROR] pwrite failed: " << open_path
                    << " - " << std::strerror(errno) << "\n";
          close(fd);
          unlink(open_path.c_str());
          return false;
        }
        total_written += written;
      }
      if (close(fd) != 0) {
        std::cerr << "[ERROR] close failed: " << open_path
                  << " - " << std::strerror(errno) << "\n";
        unlink(open_path.c_str());
        return false;
      }
      if (!no_rename) {
        if (std::rename(open_path.c_str(), dest_file.c_str()) != 0) {
          std::cerr << "[ERROR] rename failed: " << open_path << " -> "
                    << dest_file << " - " << std::strerror(errno) << "\n";
          unlink(open_path.c_str());
          return false;
        }
      }
      return true;
    }));
  }

  bool all_success = true;
  for (auto& fut : futures) {
    if (!fut.get()) all_success = false;
  }
  if (!all_success) std::cerr << "[WARN] Some write operations failed\n";
  return all_success;
}

// ─── NIXL file-management helpers ──────────────────────────────────────────

// Probe whether O_TMPFILE is usable on dir_path.
static bool probe_tmpfile(const std::string& dir_path) {
#ifdef O_TMPFILE
  int fd = open(dir_path.c_str(), O_TMPFILE | O_WRONLY);
  if (fd >= 0) { close(fd); return true; }
#endif
  (void)dir_path;
  return false;
}

// open_files_write: open N files for writing.
// Returns (fds, open_paths, is_tmpfile).
//   no_rename       → direct write; open_paths[i] == final_paths[i]
//   !no_rename + O_TMPFILE → unnamed inode; open_paths[i] == ""
//   !no_rename + no O_TMPFILE → named temp; open_paths[i] == temp path
// Sequential opens to avoid O_CREAT directory-inode contention on network FS.
std::tuple<std::vector<int>, std::vector<std::string>, bool>
open_files_write(std::vector<std::string> final_paths,
                 bool no_rename, bool use_o_direct,
                 std::string storage_path) {
  py::gil_scoped_release release;
  size_t n = final_paths.size();
  std::vector<int> fds(n, -1);
  std::vector<std::string> open_paths(n);

  bool is_tmpfile = !no_rename && probe_tmpfile(storage_path);
  SimpleThreadPool& pool = get_thread_pool();

  if (is_tmpfile) {
    // O_TMPFILE: all threads open the same directory — no O_CREAT inode contention.
    // Safe to parallelize.
#ifdef O_TMPFILE
    std::vector<std::future<int>> futures;
    futures.reserve(n);
    for (size_t i = 0; i < n; i++) {
      futures.push_back(pool.enqueue([&storage_path, use_o_direct]() -> int {
        int flags = O_TMPFILE | O_WRONLY;
        if (use_o_direct) {
          int fd = open(storage_path.c_str(), flags | O_DIRECT);
          if (fd >= 0 || errno != EINVAL) return fd;
        }
        return open(storage_path.c_str(), flags);
      }));
    }
    for (size_t i = 0; i < n; i++) {
      fds[i] = futures[i].get();
      open_paths[i] = "";  // unnamed inode — published via /proc/self/fd/
      if (fds[i] < 0) {
        for (size_t j = 0; j < i; j++) if (fds[j] >= 0) close(fds[j]);
        throw std::runtime_error(std::string("open_files_write (O_TMPFILE): ") + std::strerror(errno));
      }
    }
#endif
  } else {
    // no_rename or named temp: O_CREAT contends on the directory inode — sequential.
    for (size_t i = 0; i < n; i++) {
      int fd = -1;
      if (no_rename) {
        int flags = O_WRONLY | O_CREAT | O_TRUNC;
#ifdef O_DIRECT
        if (use_o_direct) {
          fd = open(final_paths[i].c_str(), flags | O_DIRECT, 0644);
          if (fd < 0 && errno == EINVAL)
            fd = open(final_paths[i].c_str(), flags, 0644);
        } else {
          fd = open(final_paths[i].c_str(), flags, 0644);
        }
#else
        fd = open(final_paths[i].c_str(), flags, 0644);
#endif
        open_paths[i] = final_paths[i];
      } else {
        std::string sep = (!storage_path.empty() && storage_path.back() == '/') ? "" : "/";
        std::string temp_path = storage_path + sep + "tmp_nixl_" + std::to_string(i) + ".bin";
        int flags = O_WRONLY | O_CREAT | O_TRUNC;
#ifdef O_DIRECT
        if (use_o_direct) {
          fd = open(temp_path.c_str(), flags | O_DIRECT, 0644);
          if (fd < 0 && errno == EINVAL)
            fd = open(temp_path.c_str(), flags, 0644);
        } else {
          fd = open(temp_path.c_str(), flags, 0644);
        }
#else
        fd = open(temp_path.c_str(), flags, 0644);
#endif
        open_paths[i] = temp_path;
      }
      if (fd < 0) {
        for (size_t j = 0; j < i; j++) if (fds[j] >= 0) close(fds[j]);
        throw std::runtime_error(std::string("open_files_write: ") + std::strerror(errno));
      }
      fds[i] = fd;
    }
  }

  return {fds, open_paths, is_tmpfile};
}

// publish_and_close: atomically publish written files and close FDs.
// Uses the shared thread pool for parallel link/rename operations.
void publish_and_close(std::vector<int> fds,
                       std::vector<std::string> open_paths,
                       std::vector<std::string> final_paths,
                       bool is_tmpfile, bool no_rename) {
  py::gil_scoped_release release;
  size_t n = fds.size();
  SimpleThreadPool& pool = get_thread_pool();
  std::vector<std::future<bool>> futures;
  futures.reserve(n);

  for (size_t i = 0; i < n; i++) {
    int fd = fds[i];
    if (no_rename) {
      futures.push_back(pool.enqueue([fd]() -> bool {
        return close(fd) == 0;
      }));
    } else if (is_tmpfile) {
      std::string final_path = final_paths[i];
      futures.push_back(pool.enqueue([fd, final_path]() -> bool {
        char proc_path[64];
        snprintf(proc_path, sizeof(proc_path), "/proc/self/fd/%d", fd);
        bool ok = (link(proc_path, final_path.c_str()) == 0);
        if (!ok)
          std::cerr << "[ERROR] link " << proc_path << " -> " << final_path
                    << ": " << std::strerror(errno) << "\n";
        close(fd);
        return ok;
      }));
    } else {
      std::string temp_path = open_paths[i];
      std::string final_path = final_paths[i];
      futures.push_back(pool.enqueue([fd, temp_path, final_path]() -> bool {
        bool ok = (std::rename(temp_path.c_str(), final_path.c_str()) == 0);
        if (!ok)
          std::cerr << "[ERROR] rename " << temp_path << " -> " << final_path
                    << ": " << std::strerror(errno) << "\n";
        close(fd);
        return ok;
      }));
    }
  }

  bool all_ok = true;
  for (auto& f : futures)
    if (!f.get()) all_ok = false;
  if (!all_ok)
    std::cerr << "[WARN] publish_and_close: some operations failed\n";
}

// open_files_read: open existing files for reading in parallel.
// Safe to parallelize since O_RDONLY has no directory-inode contention.
std::vector<int> open_files_read(std::vector<std::string> paths, bool use_o_direct) {
  size_t n = paths.size();
  std::vector<int> fds(n, -1);
  std::string failed_path;

  {
    py::gil_scoped_release release;
    SimpleThreadPool& pool = get_thread_pool();
    std::vector<std::future<int>> futures;
    futures.reserve(n);

    for (size_t i = 0; i < n; i++) {
      std::string path = paths[i];
      futures.push_back(pool.enqueue([path, use_o_direct]() -> int {
#ifdef O_DIRECT
        if (use_o_direct) {
          int fd = open(path.c_str(), O_RDONLY | O_DIRECT);
          if (fd >= 0 || errno != EINVAL) return fd;
        }
#endif
        return open(path.c_str(), O_RDONLY);
      }));
    }

    for (size_t i = 0; i < n; i++) {
      fds[i] = futures[i].get();
      if (fds[i] < 0 && failed_path.empty())
        failed_path = paths[i];
    }

    if (!failed_path.empty())
      for (int fd : fds) if (fd >= 0) close(fd);
  }

  if (!failed_path.empty())
    throw std::runtime_error("open_files_read: open failed for " + failed_path);
  return fds;
}

// close_fds: close a list of file descriptors in parallel via thread pool.
void close_fds(std::vector<int> fds) {
  py::gil_scoped_release release;
  SimpleThreadPool& pool = get_thread_pool();
  std::vector<std::future<void>> futures;
  futures.reserve(fds.size());
  for (int fd : fds) {
    if (fd >= 0)
      futures.push_back(pool.enqueue([fd]() { close(fd); }));
  }
  for (auto& f : futures) f.get();
}

// PyBind11 bindings
PYBIND11_MODULE(TORCH_EXTENSION_NAME, m) {
  m.def("cpp_read_blocks",
        &cpp_read_blocks,
        "Read multiple blocks from separate files into a tensor in parallel",
        py::arg("buffer"),
        py::arg("block_size"),
        py::arg("block_indices"),
        py::arg("source_files"));

  m.def("cpp_write_blocks",
        &cpp_write_blocks,
        "Write multiple blocks from a tensor to separate files in parallel",
        py::arg("buffer"),
        py::arg("block_size"),
        py::arg("block_indices"),
        py::arg("dest_files"));

  m.def("set_thread_count",
        &set_thread_count,
        "Set the number of threads for the I/O thread pool",
        py::arg("num_threads"));

  m.def("get_io_thread_count",
        &get_io_thread_count,
        "Get the current number of threads in the I/O thread pool");

  m.def("set_o_direct",
        &set_o_direct,
        "Enable or disable O_DIRECT for all I/O operations",
        py::arg("enabled"));

  m.def("get_o_direct",
        &get_o_direct,
        "Get whether O_DIRECT is currently enabled");

  m.def("set_no_rename",
        &set_no_rename,
        "When enabled, write directly to destination (skip .tmp + rename)",
        py::arg("enabled"));

  m.def("get_no_rename",
        &get_no_rename,
        "Get whether no-rename mode is currently enabled");

  m.def("open_files_write",
        &open_files_write,
        "Open N files for writing; returns (fds, open_paths, is_tmpfile)",
        py::arg("final_paths"),
        py::arg("no_rename"),
        py::arg("use_o_direct"),
        py::arg("storage_path"));

  m.def("publish_and_close",
        &publish_and_close,
        "Parallel link/rename + close after NIXL write transfer",
        py::arg("fds"),
        py::arg("open_paths"),
        py::arg("final_paths"),
        py::arg("is_tmpfile"),
        py::arg("no_rename"));

  m.def("open_files_read",
        &open_files_read,
        "Open N existing files for reading in parallel; returns fds",
        py::arg("paths"),
        py::arg("use_o_direct"));

  m.def("close_fds",
        &close_fds,
        "Close a list of file descriptors",
        py::arg("fds"));
}
