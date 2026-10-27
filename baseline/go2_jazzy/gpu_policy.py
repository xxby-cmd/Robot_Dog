"""NVIDIA application profile scoped exclusively to qemu-go2-jazzy.exe.

ABI / setting definitions: NVIDIA/nvapi (nvapi.h, nvapi_interface.h,
NvApiDriverSettings.h). Uses the installed driver; no injected DLLs or global profile.
"""
import argparse
import base64
import ctypes as C
import json
from pathlib import Path
import winreg
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop_install'))
from common import ensure_config, DEFAULT_CONFIG
ROOT = EXE = None
PROFILE_NAME = 'Go2 ROS2 Jazzy CHAMP (project only)'
U32, Handle = C.c_uint32, C.c_void_p
Unicode = C.c_wchar*2048
SETTING_IDS = {'optimus_enabled':0x10F9DC80, 'preferred_gpu':0x10F9DC81, 'rendering_options':0x10F9DC84}

class Binary(C.Structure):
    _fields_ = [('length',U32), ('data',C.c_ubyte*4096)]

class Value(C.Union):
    _pack_ = 4
    _fields_ = [('dword',U32), ('binary',Binary), ('text',Unicode), ('qword',C.c_uint64)]

class Setting(C.Structure):
    _pack_ = 4
    _fields_ = [('version',U32), ('name',Unicode), ('id',U32), ('kind',U32),
                ('location',U32), ('current_predefined',U32), ('predefined_valid',U32),
                ('predefined',Value), ('current',Value)]

class Application(C.Structure):
    _fields_ = [('version',U32), ('predefined',U32), ('name',Unicode),
                ('friendly_name',Unicode), ('launcher',Unicode)]

class Profile(C.Structure):
    _fields_ = [('version',U32), ('name',Unicode), ('gpu_support',U32),
                ('predefined',U32), ('apps',U32), ('settings',U32)]

def versioned(kind):
    value = kind()
    value.version = C.sizeof(kind) | (1<<16)
    return value

class Driver:
    def __init__(self):
        self.dll = C.CDLL(r'C:\Windows\System32\nvapi64.dll')
        self.query = self.dll.nvapi_QueryInterface
        self.query.argtypes, self.query.restype = [U32], C.c_void_p
        self.initialize = self.function(0x0150e828)
        self.unload = self.function(0xd22bdd7e)
        self.error_message = self.function(0x6c2d048c, C.c_int32, C.c_void_p)
        self.create_session = self.function(0x0694d52e, C.POINTER(Handle))
        self.destroy_session = self.function(0xdad9cff8, Handle)
        self.load = self.function(0x375dbd6b, Handle)
        self.save = self.function(0xfcbc7e14, Handle)
        self.find_app = self.function(0xeee566b2, Handle, C.c_wchar_p, C.POINTER(Handle), C.POINTER(Application))
        self.find_profile = self.function(0x7e4a9a0b, Handle, C.c_wchar_p, C.POINTER(Handle))
        self.create_profile = self.function(0xcc176068, Handle, C.POINTER(Profile), C.POINTER(Handle))
        self.profile_info = self.function(0x61cd6fd6, Handle, Handle, C.POINTER(Profile))
        self.create_app = self.function(0x4347a9de, Handle, Handle, C.POINTER(Application))
        self.get_setting = self.function(0x73bf8338, Handle, Handle, U32, C.POINTER(Setting))
        self.set_setting = self.function(0x577dd202, Handle, Handle, C.POINTER(Setting))
        self.delete_setting = self.function(0xe4a26362, Handle, Handle, U32)
        self.delete_profile = self.function(0x17093206, Handle, Handle)
        self.check(self.initialize())
        self.session = Handle()
        self.check(self.create_session(C.byref(self.session)))
        self.check(self.load(self.session))

    def function(self, identity, *arguments):
        address = self.query(identity)
        if not address:
            raise RuntimeError(f'NVIDIA API unavailable: {identity:#x}')
        return C.CFUNCTYPE(C.c_int32, *arguments)(address)

    def check(self, status):
        if status:
            message = C.create_string_buffer(64)
            self.error_message(status, message)
            raise RuntimeError(f'NVIDIA driver: {message.value.decode(errors="replace")} ({status})')

    def close(self):
        self.destroy_session(self.session)
        self.unload()

    def application_profile(self):
        handle, app = Handle(), versioned(Application)
        status = self.find_app(self.session, EXE.name, C.byref(handle), C.byref(app))
        if status == -166:
            return None
        self.check(status)
        info = versioned(Profile)
        self.check(self.profile_info(self.session, handle, C.byref(info)))
        return handle, info, app

    def setting(self, handle, identity):
        value = versioned(Setting)
        status = self.get_setting(self.session, handle, identity, C.byref(value))
        if status == -160:
            return None
        self.check(status)
        return value

    def inspect(self):
        found = self.application_profile()
        if found is None:
            return {'executable':str(EXE), 'profile_exists':False}
        handle, info, app = found
        result = {'executable':str(EXE), 'profile_exists':True, 'profile_name':info.name,
                  'applications_in_profile':info.apps, 'settings':{}}
        for name, identity in SETTING_IDS.items():
            value = self.setting(handle, identity)
            result['settings'][name] = None if value is None else {
                'value':value.current.dword, 'location':value.location, 'type':value.kind,
                'raw':base64.b64encode(bytes(value)).decode()}
        return result

    def ensure(self):
        before = self.inspect()
        if before.get('profile_exists') and before['profile_name'] != PROFILE_NAME:
            raise RuntimeError('Executable belongs to a different NVIDIA profile; refusing to alter a shared profile')
        backup = ROOT/'nvidia-app-profile-before.json'
        if not backup.exists():
            backup.write_text(json.dumps(before, indent=2))
        found = self.application_profile()
        if found is None:
            handle = Handle()
            status = self.find_profile(self.session, PROFILE_NAME, C.byref(handle))
            if status == -163:
                info = versioned(Profile)
                info.name = PROFILE_NAME
                self.check(self.create_profile(self.session, C.byref(info), C.byref(handle)))
            else:
                self.check(status)
            app = versioned(Application)
            app.name, app.friendly_name = EXE.name, 'Go2 ROS2 Jazzy CHAMP'
            self.check(self.create_app(self.session, handle, C.byref(app)))
        else:
            handle, info, app = found
            if info.apps != 1:
                raise RuntimeError('The project NVIDIA profile is shared; refusing to alter other applications')
        options = self.setting(handle, SETTING_IDS['rendering_options'])
        if options is not None and options.kind != 0:
            raise RuntimeError('Unexpected NVIDIA rendering-options type')
        # Explicit NVIDIA preference; do not inherit the Python launcher's choice.
        desired = {'optimus_enabled':1, 'preferred_gpu':1,
                   'rendering_options':(options.current.dword if options else 0) & ~0x100}
        changed = False
        for name, identity in SETTING_IDS.items():
            current = self.setting(handle, identity)
            if current is not None and current.kind == 0 and current.location == 0 and current.current.dword == desired[name]:
                continue
            value = versioned(Setting)
            value.id, value.kind, value.current.dword = identity, 0, desired[name]
            self.check(self.set_setting(self.session, handle, C.byref(value)))
            changed = True
        if changed or not before.get('profile_exists'):
            self.check(self.save(self.session))
        self.check(self.load(self.session))
        result = self.inspect()
        if any(result['settings'][key]['value'] != value or result['settings'][key]['location'] != 0
               for key,value in desired.items()):
            raise RuntimeError('NVIDIA application profile did not persist')
        result['changed'] = changed
        return result

    def restore(self):
        before = json.loads((ROOT/'nvidia-app-profile-before.json').read_text())
        found = self.application_profile()
        if found is None:
            return
        handle, info, app = found
        if info.name != PROFILE_NAME or info.apps != 1:
            raise RuntimeError('Refusing to restore a shared/different NVIDIA profile')
        if not before['profile_exists']:
            self.check(self.delete_profile(self.session, handle))
        else:
            for name, identity in SETTING_IDS.items():
                previous = before['settings'][name]
                if previous is None or previous['location'] != 0:
                    status = self.delete_setting(self.session, handle, identity)
                    if status != -160:
                        self.check(status)
                else:
                    value = Setting.from_buffer_copy(base64.b64decode(previous['raw']))
                    self.check(self.set_setting(self.session, handle, C.byref(value)))
        self.check(self.save(self.session))

def ensure():
    # Leave Python, old QEMU, eNSP and global NVIDIA settings untouched.
    key_path = r'Software\Microsoft\DirectX\UserGpuPreferences'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, str(EXE), 0, winreg.REG_SZ, 'GpuPreference=2;')
    driver = Driver()
    try:
        result = driver.ensure()
        (ROOT/'nvidia-app-profile.json').write_text(json.dumps(result, indent=2))
        return result
    finally:
        driver.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['inspect','ensure','restore'], default='inspect', nargs='?')
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    config = ensure_config(args.config)
    ROOT = Path(config['runtime'])/'jazzy'
    EXE = Path(config['qemu'])
    if EXE.name.casefold() != 'qemu-go2-jazzy.exe':
        raise RuntimeError('独显配置仅适用于机器狗专用 QEMU 程序，不修改通用程序。')
    action = args.action
    if action == 'ensure':
        result = ensure()
    else:
        driver = Driver()
        try:
            result = driver.inspect() if action == 'inspect' else driver.restore()
        finally:
            driver.close()
    # Binary ABI backups stay on disk, not in routine user-facing logs.
    if result and 'settings' in result:
        for item in result['settings'].values():
            if item:
                item.pop('raw', None)
    print(json.dumps(result, indent=2))
