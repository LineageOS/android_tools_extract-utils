from os import path

# TODO: find out if partition-less files are a thing
ALL_PARTITIONS = ['system', 'vendor', 'product', 'system_ext', 'odm']
APEX_PARTITIONS = ['system', 'vendor', 'system_ext']
RFSA_PARTITIONS = ['vendor', 'odm']

PREFIX_PARTITIONS_ALTERNATIVE_PARTS_MAP = {
    'product': [
        ['product'],
        ['system', 'product'],
    ],
    'system_ext': [
        ['system_ext'],
        ['system', 'system_ext'],
    ],
    'odm': [
        ['odm'],
        ['vendor', 'odm'],
        ['system', 'vendor', 'odm'],
    ],
    'vendor': [
        ['vendor'],
        ['system', 'vendor'],
    ],
    'system': [
        ['system'],
    ],
    'vendor_dlkm': [
        ['vendor_dlkm'],
    ],
    'recovery': [
        ['recovery'],
    ],
    'vendor_ramdisk': [
        ['vendor_ramdisk'],
    ],
}
