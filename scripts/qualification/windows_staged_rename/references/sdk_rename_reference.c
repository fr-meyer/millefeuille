/* Source-only reference DLL. Not compiled or executed on native Windows here.
 * Its caller must supply only fresh, owned synthetic fixture handles and paths.
 * Use the installed Windows SDK, not a copied hand-written rename structure.
 */
#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0A00
#include <windows.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#if !defined(_M_X64)
#error This diagnostic reference requires Windows AMD64
#endif

__declspec(dllexport) DWORD WINAPI mf_sdk_layout(DWORD *out, DWORD capacity) {
    if (out == NULL || capacity < 7) return ERROR_INVALID_PARAMETER;
    out[0] = (DWORD)sizeof(FILE_RENAME_INFO);
    out[1] = (DWORD)offsetof(FILE_RENAME_INFO, RootDirectory);
    out[2] = (DWORD)offsetof(FILE_RENAME_INFO, FileNameLength);
    out[3] = (DWORD)offsetof(FILE_RENAME_INFO, FileName);
    out[4] = (DWORD)sizeof(HANDLE);
    out[5] = (DWORD)sizeof(WCHAR);
    out[6] = (DWORD)sizeof(DWORD);
    return ERROR_SUCCESS;
}

/* Returns the API's BOOL; meaningful last-error is captured on failure only. */
__declspec(dllexport) BOOL WINAPI mf_sdk_rename(
    HANDLE source, HANDLE parent, const WCHAR *name, DWORD name_bytes,
    DWORD *last_error, DWORD *buffer_bytes) {
    if (last_error == NULL || buffer_bytes == NULL) return FALSE;
    *last_error = ERROR_INVALID_PARAMETER;
    *buffer_bytes = 0;
    if (source == NULL || source == INVALID_HANDLE_VALUE || name == NULL ||
        name_bytes == 0 || name_bytes > 65532 || (name_bytes & 1) != 0) return FALSE;
    for (DWORD i = 0; i < name_bytes / sizeof(WCHAR); ++i) {
        if (name[i] == 0) return FALSE;
        if (parent != NULL && (name[i] == L'\\' || name[i] == L'/' || name[i] == L':'))
            return FALSE;
    }
    DWORD units = name_bytes / sizeof(WCHAR);
    if (parent != NULL &&
        (name[units - 1] == L'.' || name[units - 1] == L' ')) return FALSE;
    DWORD bytes = (DWORD)sizeof(FILE_RENAME_INFO) + name_bytes + sizeof(WCHAR);
    FILE_RENAME_INFO *info = (FILE_RENAME_INFO *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, bytes);
    if (info == NULL) { *last_error = ERROR_NOT_ENOUGH_MEMORY; return FALSE; }
    info->ReplaceIfExists = FALSE;
    info->RootDirectory = parent;
    info->FileNameLength = name_bytes;
    memcpy(info->FileName, name, name_bytes);
    BOOL result = SetFileInformationByHandle(source, FileRenameInfo, info, bytes);
    DWORD error = result ? ERROR_SUCCESS : GetLastError();
    *last_error = error;
    *buffer_bytes = bytes;
    HeapFree(GetProcessHeap(), 0, info);
    return result;
}
