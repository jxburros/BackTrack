cmake_minimum_required(VERSION 3.16)

project(backtrack
    VERSION 0.0.1
    DESCRIPTION "BackTrack - stem separator (Explode) and MIDI transcriber (Surveil)"
    LANGUAGES C CXX
)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
option(BUILD_SHARED_LIBS "Build shared dependencies" OFF)
option(BACKTRACK_BUILD_TESTS "Build the core unit tests" ON)

if (NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release)
endif()

# ---------------------------------------------------------------------------
# Acceleration options (forwarded to ggml), as in Onyx Engine. The CPU
# backend is always built.
# ---------------------------------------------------------------------------
option(BACKTRACK_CUDA   "Build with CUDA support"     OFF)
option(BACKTRACK_VULKAN "Build with Vulkan support"   OFF)
option(BACKTRACK_METAL  "Build with Metal support"    OFF)
option(BACKTRACK_HIP    "Build with ROCm/HIP support" OFF)
option(BACKTRACK_BLAS   "Build with BLAS support"     OFF)

set(GGML_CUDA   ${BACKTRACK_CUDA}   CACHE BOOL "" FORCE)
set(GGML_VULKAN ${BACKTRACK_VULKAN} CACHE BOOL "" FORCE)
set(GGML_METAL  ${BACKTRACK_METAL}  CACHE BOOL "" FORCE)
set(GGML_HIP    ${BACKTRACK_HIP}    CACHE BOOL "" FORCE)
set(GGML_BLAS   ${BACKTRACK_BLAS}   CACHE BOOL "" FORCE)
set(GGML_BUILD_TESTS    OFF CACHE BOOL "" FORCE)
set(GGML_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)

# ---------------------------------------------------------------------------
# Vendored ggml, pinned in packaging/vendor/ggml.conf. Resolution order:
#   1. -DBACKTRACK_GGML_DIR=<path>              explicit override
#   2. node_modules/@jxburros/ggml-source       (canonical: npm ci)
#   3. vendor/ggml                              manually placed source tree
# ---------------------------------------------------------------------------
set(BACKTRACK_GGML_DIR "" CACHE PATH "Path to the ggml source tree (overrides auto-detection)")
set(BACKTRACK_GGML_PKG ${CMAKE_CURRENT_SOURCE_DIR}/node_modules/@jxburros/ggml-source)

if (BACKTRACK_GGML_DIR)
    set(BACKTRACK_GGML_SRC ${BACKTRACK_GGML_DIR})
elseif (EXISTS ${BACKTRACK_GGML_PKG}/CMakeLists.txt)
    set(BACKTRACK_GGML_SRC ${BACKTRACK_GGML_PKG})
elseif (EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/vendor/ggml/CMakeLists.txt)
    set(BACKTRACK_GGML_SRC ${CMAKE_CURRENT_SOURCE_DIR}/vendor/ggml)
else()
    message(FATAL_ERROR "ggml source tree not found - run: npm ci")
endif()
if (NOT EXISTS ${BACKTRACK_GGML_SRC}/include/gguf.h)
    message(FATAL_ERROR "'${BACKTRACK_GGML_SRC}' is not a ggml source tree")
endif()

file(READ ${CMAKE_CURRENT_SOURCE_DIR}/packaging/vendor/ggml.conf BACKTRACK_VENDOR_SPEC)
if (NOT BACKTRACK_VENDOR_SPEC MATCHES "VENDOR_COMMIT=\"([0-9a-f]+)\"")
    message(FATAL_ERROR "cannot read the pinned ggml commit")
endif()
set(BACKTRACK_GGML_COMMIT "${CMAKE_MATCH_1}")
if (EXISTS ${BACKTRACK_GGML_SRC}/.vendor-source-commit)
    file(READ ${BACKTRACK_GGML_SRC}/.vendor-source-commit BACKTRACK_INSTALLED_COMMIT)
    string(STRIP "${BACKTRACK_INSTALLED_COMMIT}" BACKTRACK_INSTALLED_COMMIT)
    if (NOT BACKTRACK_INSTALLED_COMMIT STREQUAL BACKTRACK_GGML_COMMIT)
        message(FATAL_ERROR "installed ggml source does not match the pinned commit; run npm ci")
    endif()
elseif (BACKTRACK_GGML_SRC STREQUAL BACKTRACK_GGML_PKG)
    message(FATAL_ERROR "installed ggml source has no verifiable commit stamp; run npm ci")
endif()
string(SUBSTRING "${BACKTRACK_GGML_COMMIT}" 0 9 BACKTRACK_GGML_SHORT)
message(STATUS "backtrack: ggml ${BACKTRACK_GGML_SHORT} from ${BACKTRACK_GGML_SRC}")

add_subdirectory(${BACKTRACK_GGML_SRC} ${CMAKE_BINARY_DIR}/ggml)

if (MSVC)
    add_compile_options(/utf-8 /bigobj)
endif()

# ---------------------------------------------------------------------------
# third_party: cpp-httplib (compiled once), nlohmann/json and dr_libs
# (header-only).
# ---------------------------------------------------------------------------
add_library(backtrack-httplib STATIC third_party/cpp-httplib/httplib.cpp)
target_include_directories(backtrack-httplib PUBLIC third_party/cpp-httplib)
find_package(Threads REQUIRED)
target_link_libraries(backtrack-httplib PUBLIC Threads::Threads)
if (WIN32)
    target_link_libraries(backtrack-httplib PUBLIC ws2_32 crypt32)
endif()

add_library(backtrack-json INTERFACE)
target_include_directories(backtrack-json INTERFACE third_party)

# ---------------------------------------------------------------------------
# core/: audio, DSP, music, MIDI, ggml/GGUF helpers.
# ---------------------------------------------------------------------------
add_library(backtrack-core STATIC
    core/src/audio.cpp
    core/src/base64.cpp
    core/src/fft.cpp
    core/src/ggml_util.cpp
    core/src/hcqt.cpp
    core/src/midi.cpp
    core/src/music.cpp
)
target_include_directories(backtrack-core PUBLIC core/include PRIVATE third_party/dr_libs)
target_compile_definitions(backtrack-core PUBLIC BACKTRACK_VERSION="${PROJECT_VERSION}")
target_link_libraries(backtrack-core PUBLIC ggml)

# ---------------------------------------------------------------------------
# server/: the shared HTTP layer, and ui/: the page it serves, embedded at
# configure time (editing ui/index.html re-runs configure).
# ---------------------------------------------------------------------------
add_library(backtrack-server STATIC server/src/server.cpp)
target_include_directories(backtrack-server PUBLIC server/include)
target_link_libraries(backtrack-server PUBLIC backtrack-core backtrack-json PRIVATE backtrack-httplib)

set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/ui/index.html)
file(READ ${CMAKE_CURRENT_SOURCE_DIR}/ui/index.html BACKTRACK_PAGE_HEX HEX)
string(LENGTH "${BACKTRACK_PAGE_HEX}" BACKTRACK_PAGE_HEX_LEN)
string(REGEX REPLACE "([0-9a-f][0-9a-f])" "0x\\1," BACKTRACK_PAGE_BYTES "${BACKTRACK_PAGE_HEX}")
string(REGEX REPLACE "((0x[0-9a-f][0-9a-f],){32})" "\\1\n" BACKTRACK_PAGE_BYTES "${BACKTRACK_PAGE_BYTES}")
file(WRITE ${CMAKE_BINARY_DIR}/generated/backtrack_page.h.tmp
"// Generated from ui/index.html by CMakeLists.txt. Do not edit.\n#pragma once\nnamespace backtrack {\ninline const unsigned char kPageBytes[] = {\n${BACKTRACK_PAGE_BYTES}\n0x00};\ninline const char * const kPageHtml = reinterpret_cast<const char *>(kPageBytes);\n} // namespace backtrack\n")
configure_file(${CMAKE_BINARY_DIR}/generated/backtrack_page.h.tmp ${CMAKE_BINARY_DIR}/generated/backtrack_page.h COPYONLY)
add_library(backtrack-ui INTERFACE)
target_include_directories(backtrack-ui INTERFACE ${CMAKE_BINARY_DIR}/generated)

# ---------------------------------------------------------------------------
# apps/
# ---------------------------------------------------------------------------
add_executable(backtrack-surveil
    apps/surveil/main.cpp
    apps/surveil/model.cpp
    apps/surveil/notes.cpp
    apps/surveil/surveil.cpp
)
target_link_libraries(backtrack-surveil PRIVATE backtrack-core backtrack-server backtrack-ui backtrack-json)

if (BACKTRACK_BUILD_TESTS)
    enable_testing()
    add_executable(backtrack-tests tests/core_tests.cpp apps/surveil/model.cpp apps/surveil/notes.cpp apps/surveil/surveil.cpp)
    target_include_directories(backtrack-tests PRIVATE apps/surveil)
    target_link_libraries(backtrack-tests PRIVATE backtrack-core backtrack-json)
    add_test(NAME backtrack-tests COMMAND backtrack-tests)
endif()
