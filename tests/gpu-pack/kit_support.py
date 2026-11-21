"""Shared helpers for the tests in this folder, which test the GPU pack in place (src/gpu-pack). Not a test module.

KIT is src/gpu-pack; module() loads one of its files by path, as the pack's own scripts are not a package.
"""
import importlib.util
import os
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[2] / 'src' / 'gpu-pack'
# no bytecode next to the code under test
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True


def module(name, relative):
    spec = importlib.util.spec_from_file_location(name, KIT / relative)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


prov = module('provenance', 'build/provenance.py')
validator = module('validator', 'build/validate.py')
