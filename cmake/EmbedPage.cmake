# Embeds every file under ui/ into a generated header, as backtrack::kPageFiles, at configure
# time. Adding, removing or editing a file there re-runs configure.
function(backtrack_embed_page ui_dir out_header)
    file(GLOB_RECURSE files CONFIGURE_DEPENDS RELATIVE ${ui_dir} ${ui_dir}/*)
    list(SORT files)
    set(arrays "")
    set(table "")
    set(i 0)
    foreach(rel IN LISTS files)
        get_filename_component(ext ${rel} LAST_EXT)
        string(TOLOWER "${ext}" ext)
        if (ext STREQUAL ".html")
            set(type "text/html; charset=utf-8")
        elseif (ext STREQUAL ".css")
            set(type "text/css; charset=utf-8")
        elseif (ext STREQUAL ".js")
            set(type "text/javascript; charset=utf-8")
        elseif (ext STREQUAL ".woff2")
            set(type "font/woff2")
        elseif (ext STREQUAL ".txt")
            set(type "text/plain; charset=utf-8")
        elseif (ext STREQUAL ".svg")
            set(type "image/svg+xml")
        else()
            message(FATAL_ERROR "ui/${rel}: no Content-Type for '${ext}' files (add one in cmake/EmbedPage.cmake)")
        endif()
        set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${ui_dir}/${rel})
        file(READ ${ui_dir}/${rel} hex HEX)
        string(LENGTH "${hex}" hex_len)
        math(EXPR size "${hex_len} / 2")
        string(REGEX REPLACE "([0-9a-f][0-9a-f])" "0x\\1," bytes "${hex}")
        string(REGEX REPLACE "((0x[0-9a-f][0-9a-f],){32})" "\\1\n" bytes "${bytes}")
        string(APPEND arrays "inline const unsigned char kPageFile${i}[] = {\n${bytes}\n0x00};\n")
        string(APPEND table "    {\"${rel}\", \"${type}\", kPageFile${i}, ${size}},\n")
        math(EXPR i "${i} + 1")
    endforeach()
    file(WRITE ${out_header}.tmp
"// Generated from ui/ by cmake/EmbedPage.cmake. Do not edit.
#pragma once
#include <cstddef>
namespace backtrack {
struct EmbeddedFile {
    const char * path;
    const char * type;
    const unsigned char * data;
    size_t size;
};
${arrays}inline const EmbeddedFile kPageFiles[] = {
${table}};
} // namespace backtrack
")
    configure_file(${out_header}.tmp ${out_header} COPYONLY)
endfunction()
