cmake_minimum_required(VERSION 3.20)
project(PROJECT_NAME CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Toolchain file should be passed via -DCMAKE_TOOLCHAIN_FILE=cmake/toolchain.cmake
# or configured by GRIM for the target architecture.

include_directories(include)

add_executable(${PROJECT_NAME} src/main.cpp)

# Embedded projects commonly disable the standard host test runner.
# Uncomment and adapt for on-target or emulated testing:
# enable_testing()
# add_subdirectory(tests)
