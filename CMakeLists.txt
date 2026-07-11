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

# The MAP sibling model (match-action processor).
file(GLOB_RECURSE MAP_SAIL_FILES "${CMAKE_SOURCE_DIR}/spec/map-model/*.sail")
set(MAP_MAIN "${CMAKE_SOURCE_DIR}/spec/map-model/main.sail")

add_custom_target(check-map
    COMMAND ${SAIL} --just-check ${MAP_MAIN}
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
    COMMENT "Type-checking MAP Sail model..."
    SOURCES ${MAP_SAIL_FILES}
)

enable_testing()
add_subdirectory(spec/test)

# The emulator CLI: Sail model compiled without a generated main, driven by
# a thin C shim through the machine-word emu_* API.
set(EMU_STEM "${CMAKE_BINARY_DIR}/nanuk_model")
add_custom_command(
    OUTPUT ${EMU_STEM}.c
    COMMAND ${SAIL} -c --c-no-main
        # Keep the harness API alive through C dead-code elimination.
        --c-preserve emu_reset
        --c-preserve emu_poke_imem
        --c-preserve emu_poke_pkt
        --c-preserve emu_set_plen
        --c-preserve emu_run
        --c-preserve emu_get_verdict
        --c-preserve emu_get_error
        --c-preserve emu_get_cursor
        --c-preserve emu_get_steps
        --c-preserve emu_get_hdr_present
        --c-preserve emu_get_hdr_offset
        --c-preserve emu_get_smd
        ${MODEL_MAIN}
        ${CMAKE_SOURCE_DIR}/spec/emulator/entry.sail -o ${EMU_STEM}
    DEPENDS ${MODEL_SAIL_FILES} ${CMAKE_SOURCE_DIR}/spec/emulator/entry.sail
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
    COMMENT "Compiling Sail model to C (emulator core)..."
)
add_executable(nanuk-emu
    spec/emulator/main.c
    ${EMU_STEM}.c
    ${SAIL_DIR}/lib/sail.c
    ${SAIL_DIR}/lib/rts.c
    ${SAIL_DIR}/lib/elf.c
    ${SAIL_DIR}/lib/sail_failure.c
)
target_include_directories(nanuk-emu PRIVATE ${SAIL_DIR}/lib)
target_link_libraries(nanuk-emu PRIVATE gmp z)
target_compile_options(nanuk-emu PRIVATE -O2)

add_test(NAME emu_smoke
    COMMAND bash ${CMAKE_SOURCE_DIR}/spec/emulator/smoke_test.sh $<TARGET_FILE:nanuk-emu>)
