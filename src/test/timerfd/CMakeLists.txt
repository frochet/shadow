## build the test as a dynamic executable that plugs into shadow
add_shadow_plugin(shadow-plugin-test-timerfd shd-test-timerfd.c)

## register the tests
add_test(NAME test-timerfd COMMAND shadow-plugin-test-timerfd)
add_test(NAME test-timerfd-shadow COMMAND ${CMAKE_BINARY_DIR}/src/shadow ${CMAKE_CURRENT_SOURCE_DIR}/timerfd.test.shadow.config.xml)
