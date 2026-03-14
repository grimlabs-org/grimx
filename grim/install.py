"""
grim.intall

Delegate package installation to vcpkg or conan with 
fallback logic.
Offers to auto-install missing package managers..
"""

from __future__ import annotations

import os
import platform 
import subprocess 
import shutil 
import sys 
from pathlib import Path 

import click 

from grim.config import load_config, load_lock, add_dependency

# ------------------------------------------
# Public API
# ------------------------------------------
def _install_package(package: str) -> None:
    pass 


def _restrore_from_lock() -> None:
    pass 

# ------------------------------------------
# Auto-install
# ------------------------------------------
def _prompt_and_install_manager(manager: str) -> bool:
    pass

def _auto_install_vcpkg() -> bool:
    pass 


def _auto_install_vcpkg() -> bool:
    pass 


# ------------------------------------------
# Package manager wrappers 
# ------------------------------------------
def _try_install(manager: str, package: str) -> tuple[bool, str]:
    pass 

def _vcpkg_install(package: str) -> tuple[bool, str]:
    pass 

def _conan_install(package: str) -> tuple[bool, str]:
    pass 

def _conan_install(package: str) -> tuple[bool, str]:
    pass 

def _parse_vcpkg_version(output: str, packag: str) -> str:
    pass 

def _parse_conan_version(output: str, package: str) -> str:
    pass 





