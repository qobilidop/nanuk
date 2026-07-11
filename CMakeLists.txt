cmake_minimum_required(VERSION 3.20)
project(nanuk LANGUAGES C)

# Find the Sail compiler
find_program(SAIL sail REQUIRED)
message(STATUS "Found Sail: ${SAIL}")

# Find the Sail library directory (for C runtime headers and sources)
# SAIL_DIR is set in the devcontainer Dockerfile.
if(DEFINED ENV{SAIL_DIR})
    set(SAIL_DIR $ENV{SAIL_DIR})
else()
    message(FATAL_ERROR "SAIL_DIR environment variable not set. Cannot find Sail runtime.")
endif()
message(STATUS "Sail directory: ${SAIL_DIR}")

# Collect all model Sail files
file(GLOB_RECURSE MODEL_SAIL_FILES "${CMAKE_SOURCE_DIR}/spec/model/*.sail")

# The main entry point $includes all model files in dependency order.
set(MODEL_MAIN "${CMAKE_SOURCE_DIR}/spec/model/main.sail")

# Target: type-check the model (fast, no C compilation)
add_custom_target(check
    COMMAND ${SAIL} --just-check ${MODEL_MAIN}
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
    COMMENT "Type-checking Sail model..."
    SOURCES ${MODEL_SAIL_FILES}
)

enable_testing()
add_subdirectory(spec/test)
