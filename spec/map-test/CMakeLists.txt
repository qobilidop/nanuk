# MAP Sail model tests, mirroring spec/test/ (parser). Each test is a Sail
# file with a main(), compiled together with the MAP model to a C executable.

function(add_map_sail_test TEST_NAME TEST_SAIL)
    set(SAIL_OUT_STEM "${CMAKE_CURRENT_BINARY_DIR}/${TEST_NAME}")
    set(TEST_C "${SAIL_OUT_STEM}.c")

    add_custom_command(
        OUTPUT ${TEST_C}
        COMMAND ${SAIL} -c
            ${MAP_MAIN}
            ${CMAKE_SOURCE_DIR}/${TEST_SAIL}
            -o ${SAIL_OUT_STEM}
        DEPENDS ${MAP_SAIL_FILES} ${CMAKE_SOURCE_DIR}/${TEST_SAIL}
        WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        COMMENT "Compiling MAP Sail test ${TEST_NAME} to C..."
    )

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

add_map_sail_test(test_map_decode spec/map-test/test_map_decode.sail)
add_map_sail_test(test_map_exec_mem spec/map-test/test_map_exec_mem.sail)
add_map_sail_test(test_map_exec_lookup spec/map-test/test_map_exec_lookup.sail)
