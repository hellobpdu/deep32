#!/usr/bin/env python3
"""Restrict native Windows installation state without external tools."""
import ctypes
import re


def validate_acl(actual, owner, directory=False):
    """Accept only the service owner, System and local Administrators."""
    prefix, marker, dacl = actual.partition('D:')
    if not marker or prefix != 'O:' + owner:
        raise ValueError('installation ACL has a different owner')
    aces = re.findall(r'\(([^)]*)\)', dacl)
    if not aces or re.sub(r'\([^)]*\)', '', dacl) not in ('P', 'PAI', 'AI', ''):
        raise ValueError('installation ACL is not restricted')
    trustees = set()
    for ace in aces:
        fields = ace.split(';')
        if len(fields) != 6:
            raise ValueError('invalid installation ACL')
        kind, flags, rights, object_id, inherited_id, trustee = fields
        # Python's Windows mode 0700 uses Owner Rights (OW), which follows the
        # descriptor's verified current owner rather than naming another user.
        if trustee == 'OW':
            trustee = owner
        if (kind != 'A' or rights != 'FA' or object_id or inherited_id or
                trustee not in (owner, 'SY', 'BA') or
                re.fullmatch(r'(?:OI|CI|NP|IO|ID)*', flags) is None or
                'IO' in flags or not directory and ('OI' in flags or 'CI' in flags)):
            raise ValueError('installation ACL is not owner/System/Administrators-only')
        trustees.add(trustee)
    if not {owner, 'SY'} <= trustees:
        raise ValueError('installation ACL is missing its owner or System')


def windows_security(path, verify=False, directory=False):
    """Use the current user's SID, with a protected owner/System-only DACL."""
    from ctypes import wintypes
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    pointer = ctypes.c_void_p
    adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    adv.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(wintypes.LPWSTR)]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(pointer), ctypes.POINTER(wintypes.DWORD)]
    adv.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, pointer]
    adv.GetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, pointer, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    adv.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [pointer, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD)]
    kernel.LocalFree.argtypes = [pointer]
    handle = wintypes.HANDLE()
    if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(handle)):
        raise OSError('cannot inspect current user for a restricted ACL')
    sid_text = wintypes.LPWSTR()
    try:
        size = wintypes.DWORD()
        adv.GetTokenInformation(handle, 1, None, 0, ctypes.byref(size))
        token = ctypes.create_string_buffer(size.value)
        if not adv.GetTokenInformation(handle, 1, token, size, ctypes.byref(size)):
            raise OSError('cannot inspect current user SID')
        sid = ctypes.cast(token, ctypes.POINTER(pointer))[0]
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise OSError('cannot format current user SID')
        owner = sid_text.value
    finally:
        if sid_text: kernel.LocalFree(sid_text)
        kernel.CloseHandle(handle)
    flags = 1 | 4  # OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION
    inherit = 'OICI' if directory else ''
    expected = 'O:' + owner + 'D:P(A;' + inherit + ';FA;;;' + owner + ')(A;' + inherit + ';FA;;;SY)'
    if not verify:
        descriptor = pointer()
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(expected, 1, ctypes.byref(descriptor), None):
            raise OSError('cannot create restricted installation ACL')
        try:
            if not adv.SetFileSecurityW(str(path), flags | 0x80000000, descriptor):
                raise OSError('cannot apply restricted installation ACL')
        finally:
            kernel.LocalFree(descriptor)
    needed = wintypes.DWORD()
    adv.GetFileSecurityW(str(path), flags, None, 0, ctypes.byref(needed))
    buf = ctypes.create_string_buffer(needed.value)
    if not adv.GetFileSecurityW(str(path), flags, buf, needed, ctypes.byref(needed)):
        raise OSError('cannot verify restricted installation ACL')
    text = wintypes.LPWSTR()
    if not adv.ConvertSecurityDescriptorToStringSecurityDescriptorW(buf, 1, flags, ctypes.byref(text), None):
        raise OSError('cannot inspect restricted installation ACL')
    try:
        actual = text.value
        validate_acl(actual, owner, directory)
    finally:
        kernel.LocalFree(text)
