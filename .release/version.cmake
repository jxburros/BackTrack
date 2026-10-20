# package.json is synchronized with the authoritative release.json by release validation.
file(READ "${CMAKE_CURRENT_LIST_DIR}/../package.json" _release_package_json)
string(REGEX MATCH "\"version\"[ \t\r\n]*:[ \t\r\n]*\"([^\"]+)\"" _release_match "${_release_package_json}")
set(APP_RELEASE_VERSION "${CMAKE_MATCH_1}")
string(REGEX MATCH "^[0-9]+\\.[0-9]+\\.[0-9]+" APP_RELEASE_VERSION_CORE "${APP_RELEASE_VERSION}")
if(NOT APP_RELEASE_VERSION_CORE)
    message(FATAL_ERROR "package.json must contain a semantic application version")
endif()
