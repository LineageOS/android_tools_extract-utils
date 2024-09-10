from os import path


def get_script_dir():
    return path.dirname(path.realpath(__file__))


def get_android_root():
    script_dir = get_script_dir()
    return path.realpath(path.join(script_dir, '..', '..', '..'))


def get_binaries_dir():
    android_root = get_android_root()
    return path.join(android_root, 'prebuilts/extract-tools/linux-x86/bin')


def get_common_binaries_dir():
    android_root = get_android_root()
    return path.join(android_root, 'prebuilts/extract-tools/common')


def get_jdk_binaries_dir():
    android_root = get_android_root()
    return path.join(android_root, 'prebuilts/jdk/jdk21/linux-x86/bin')


def get_build_tools_dir():
    android_root = get_android_root()
    return path.join(android_root, 'prebuilts/build-tools/linux-x86/bin')


def get_ota_extractor_path():
    binaries_dir = get_binaries_dir()
    return path.join(binaries_dir, 'ota_extractor')


def get_lpunpack_path():
    binaries_dir = get_binaries_dir()
    return path.join(binaries_dir, 'lpunpack')


def get_simg2img_path():
    binaries_dir = get_binaries_dir()
    return path.join(binaries_dir, 'simg2img')


def get_patchelf_path(version='0_9'):
    binaries_dir = get_binaries_dir()
    return path.join(binaries_dir, f'patchelf-{version}')


def get_stripzip_path():
    binaries_dir = get_binaries_dir()
    return path.join(binaries_dir, 'stripzip')


def get_brotli_path():
    build_tools_dir = get_build_tools_dir()
    return path.join(build_tools_dir, 'brotli')


def get_sdat2img_path():
    script_path = get_script_dir()
    return path.join(script_path, '..', 'sdat2img.py')


def get_java_path():
    jdk_binaries_path = get_jdk_binaries_dir()
    return path.join(jdk_binaries_path, 'java')


def get_apktool_path():
    common_binaries_dir = get_common_binaries_dir()
    return path.join(common_binaries_dir, 'apktool/apktool.jar')
