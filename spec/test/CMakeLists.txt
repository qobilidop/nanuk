# Sail model tests: each test is a Sail file with a main(), compiled together
# with the model to a C executable and registered with CTest.
#
# Usage:
#   cmake --build build
#   ctest --test-dir build

function(add_sail_test TEST_NAME TEST_SAIL)
    set(SAIL_OUT_STEM "${CMAKE_CURRENT_BINARY_DIR}/${TEST_NAME}")
    set(TEST_C "${SAIL_OUT_STEM}.c")

    # Step 1: Sail -> C. main.sail $includes all model files.
    add_custom_command(
        OUTPUT ${TEST_C}
        COMMAND ${SAIL} -c
            ${MODEL_MAIN}
            ${CMAKE_SOURCE_DIR}/${TEST_SAIL}
            -o ${SAIL_OUT_STEM}
        DEPENDS ${MODEL_SAIL_FILES} ${CMAKE_SOURCE_DIR}/${TEST_SAIL}
        WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        COMMENT "Compiling Sail test ${TEST_NAME} to C..."
    )

    # Step 2: C -> executable, linked against the Sail C runtime.
    add_executable(${TEST_NAME}
        ${TEST_C}
        ${SAIL_DIR}/lib/sail.c
        ${SAIL_DIR}/lib/rts.c
        ${SAIL_DIR}/lib/elf.c
        ${SAIL_DIR}/lib/sail_failure.c
    )
    target_include_directories(${TEST_NAME} PRIVATE ${SAIL_DIR}/lib)
    target_link_libraries(${TEST_NAME} PRIVATE gmp z)
    target_compile_options(${TEST_NAME} PRIVATE -O2)

    add_test(NAME ${TEST_NAME} COMMAND ${TEST_NAME})
endfunction()

add_sail_test(test_smoke spec/test/test_smoke.sail)
