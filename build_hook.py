"""Hatchling build hook: 把 wheel 打成 linux_x86_64 平台特定 tag,
不是默认的 'any',因为内置的 mihomo 二进制只是 Linux x86_64 ELF。"""
from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        build_data["tag"] = "py3-none-linux_x86_64"
        build_data["pure_python"] = False
