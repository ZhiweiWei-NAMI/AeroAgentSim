cmake_minimum_required(VERSION 3.16)

project(aero_bench_gazebo_airspace_transition LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(gz-plugin2 REQUIRED COMPONENTS register)
find_package(gz-sim8 REQUIRED)

# gz-transport13's exported target refers to CPPZMQ::CPPZMQ, but some
# distribution images ship the cppzmq header without its CMake package.
# Supply the missing header-only target without downloading anything.
if(NOT TARGET CPPZMQ::CPPZMQ)
  add_library(CPPZMQ::CPPZMQ INTERFACE IMPORTED)
  set_target_properties(CPPZMQ::CPPZMQ PROPERTIES
    INTERFACE_INCLUDE_DIRECTORIES "/usr/include"
  )
endif()

find_package(gz-transport13 REQUIRED)
find_package(gz-msgs10 REQUIRED)

add_library(aero_bench_gazebo_airspace_transition SHARED
  src/AirspaceTransition.cc
)

target_compile_options(aero_bench_gazebo_airspace_transition PRIVATE
  -Wall
  -Wextra
  -Wpedantic
  -Wconversion
  -Werror
)

target_link_libraries(aero_bench_gazebo_airspace_transition PRIVATE
  gz-sim8::gz-sim8
  gz-transport13::gz-transport13
  gz-msgs10::gz-msgs10
  gz-plugin2::gz-plugin2
)

set_target_properties(aero_bench_gazebo_airspace_transition PROPERTIES
  OUTPUT_NAME aero_bench_gazebo_airspace_transition
  POSITION_INDEPENDENT_CODE ON
)

install(TARGETS aero_bench_gazebo_airspace_transition
  LIBRARY DESTINATION lib
)
