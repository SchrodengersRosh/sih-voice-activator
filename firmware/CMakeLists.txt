# ESP-IDF project CMakeLists.txt
cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake OPTIONAL RESULT_VARIABLE IDF_FOUND)
if(NOT IDF_FOUND)
    message(STATUS "IDF_PATH not set - firmware configured for ESP-IDF v5.1+ environment")
    project(sih_voice_activator_esp32)
else()
    project(sih_voice_activator_esp32)
endif()
