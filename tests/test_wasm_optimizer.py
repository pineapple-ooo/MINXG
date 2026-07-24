"""tests/test_wasm_optimizer.py - tests for WASM optimizer and linker."""
from __future__ import annotations

import pytest

from minxg.contracts.runtime.wasm import (
    WasmModule,
    WasmFunc,
    WasmExport,
    WasmOptimizer,
    WasmLinker,
    _wasm_memory_grow,
    _wasm_table_size,
    _wasm_global_init,
    _wasm_validate_import,
    _wasm_compute_fuel,
    _wasm_fuel_check,
    _wasm_stack_height,
    _wasm_fold_constants,
)


class TestWasmOptimizer:
    """Test WASM optimization passes."""

    def test_peephole_optimizes_const_add(self):
        """Test peephole: i32.const 0; i32.add -> drop"""
        module = WasmModule(name="test")
        func = WasmFunc(name="test", type=None, params=[], results=[], locals=[], body=["i32.const 0", "i32.add"])
        module.funcs = [func]
        optimized = WasmOptimizer._peephole(module)
        assert len(optimized.funcs) == 1
        assert any(isinstance(i, dict) and i.get("op") == "drop" for i in optimized.funcs[0].body)

    def test_peephole_optimizes_const_mul(self):
        """Test peephole: i32.const 1; i32.mul -> drop"""
        module = WasmModule(name="test")
        func = WasmFunc(name="test", type=None, params=[], results=[], locals=[], body=["i32.const 1", "i32.mul"])
        module.funcs = [func]
        optimized = WasmOptimizer._peephole(module)
        assert len(optimized.funcs) == 1
        assert any(isinstance(i, dict) and i.get("op") == "drop" for i in optimized.funcs[0].body)

    def test_dead_code_elim_removes_unused(self):
        """Test DCE removes unused functions."""
        module = WasmModule(name="test")
        func1 = WasmFunc(name="used", type=None, params=[], results=[], locals=[], body=[])
        func2 = WasmFunc(name="unused", type=None, params=[], results=[], locals=[], body=[])
        module.funcs = [func1, func2]
        module.exports = [WasmExport(name="used", kind="func", index=0)]
        optimized = WasmOptimizer._dead_code_elim(module)
        assert len(optimized.funcs) == 1
        assert optimized.funcs[0].name == "used"

    def test_optimizer_runs_multiple_passes(self):
        """Test that optimizer runs all requested passes."""
        module = WasmModule(name="test")
        func = WasmFunc(name="test", type=None, params=[], results=[], locals=[], body=["i32.const 0", "i32.add"])
        module.funcs = [func]
        module.exports = [WasmExport(name="test", kind="func", index=0)]
        optimized = WasmOptimizer.optimize(module, passes=["peephole", "dce"])
        assert optimized is not None
        assert len(optimized.funcs) >= 1


class TestWasmLinker:
    """Test WASM linker functionality."""

    def test_linker_adds_modules(self):
        """Test adding modules to linker."""
        linker = WasmLinker()
        module = WasmModule(name="mod1")
        linker.add_module(module)
        assert len(linker.modules) == 1

    def test_linker_merges_functions(self):
        """Test that linker merges functions from all modules."""
        linker = WasmLinker()
        mod1 = WasmModule(name="mod1")
        mod2 = WasmModule(name="mod2")
        func1 = WasmFunc(name="func1", type=None, params=[], results=[], locals=[], body=[])
        func2 = WasmFunc(name="func2", type=None, params=[], results=[], locals=[], body=[])
        mod1.funcs = [func1]
        mod2.funcs = [func2]
        linker.add_module(mod1)
        linker.add_module(mod2)
        linked = linker.link()
        assert len(linked.funcs) == 2

    def test_linker_merges_exports(self):
        """Test that linker merges exports."""
        linker = WasmLinker()
        mod1 = WasmModule(name="mod1")
        mod2 = WasmModule(name="mod2")
        mod1.exports = [WasmExport(name="export1", kind="func", index=0)]
        mod2.exports = [WasmExport(name="export2", kind="func", index=0)]
        linker.add_module(mod1)
        linker.add_module(mod2)
        linked = linker.link()
        export_names = {e.name for e in linked.exports}
        assert "export1" in export_names
        assert "export2" in export_names


class TestWasmHelperFunctions:
    """Test WASM helper functions."""

    def test_memory_grow_valid(self):
        """Test valid memory grow."""
        result = _wasm_memory_grow(1)
        assert result == 65536

    def test_memory_grow_invalid(self):
        """Test invalid memory grow."""
        with pytest.raises(ValueError):
            _wasm_memory_grow(-1)

    def test_table_size_valid(self):
        """Test valid table size."""
        result = _wasm_table_size([1, 2, 3])
        assert result == 3

    def test_table_size_too_large(self):
        """Test table size exceeding limit."""
        with pytest.raises(ValueError):
            _wasm_table_size(list(range(1000001)))

    def test_global_init_integer(self):
        """Test global init with integer."""
        assert _wasm_global_init("42") == 42

    def test_global_init_float(self):
        """Test global init with float."""
        assert _wasm_global_init("3.14") == 3.14

    def test_global_init_bool(self):
        """Test global init with boolean."""
        assert _wasm_global_init("true") is True
        assert _wasm_global_init("false") is False

    def test_validate_import_valid(self):
        """Test valid import validation."""
        assert _wasm_validate_import("env", "memory", "memory") is True

    def test_validate_import_invalid_kind(self):
        """Test invalid import kind."""
        assert _wasm_validate_import("env", "func", "invalid") is False

    def test_compute_fuel(self):
        """Test fuel computation."""
        assert _wasm_compute_fuel(100, 1.0) == 100.0

    def test_fuel_check_under_limit(self):
        """Test fuel check under limit."""
        assert _wasm_fuel_check(50.0, 100.0) is True

    def test_fuel_check_over_limit(self):
        """Test fuel check over limit."""
        assert _wasm_fuel_check(150.0, 100.0) is False

    def test_stack_height_basic(self):
        """Test basic stack height computation."""
        ops = ["local.get 0", "local.get 1", "i32.add", "drop"]
        assert _wasm_stack_height(ops) == 2

    def test_stack_height_underflow(self):
        """Test stack underflow detection."""
        with pytest.raises(ValueError, match="underflow"):
            _wasm_stack_height(["drop"])

    def test_fold_constants(self):
        """Test constant folding."""
        result = _wasm_fold_constants("3 + 4")
        assert result == "7"


class TestWasmMemoryManager:
    """Test WASM memory manager."""
    
    def test_memory_grow(self):
        """Test memory growth."""
        from minxg.contracts.runtime.wasm import WasmMemoryManager
        mem = WasmMemoryManager(initial_pages=1, max_pages=10)
        assert mem.pages == 1
        mem.grow(2)
        assert mem.pages == 3
    
    def test_memory_grow_limit(self):
        """Test memory growth limit."""
        from minxg.contracts.runtime.wasm import WasmMemoryManager
        mem = WasmMemoryManager(initial_pages=1, max_pages=2)
        mem.grow(1)
        assert mem.pages == 2
        with pytest.raises(ValueError, match="memory limit"):
            mem.grow(1)
    
    def test_memory_read_write(self):
        """Test memory read/write."""
        from minxg.contracts.runtime.wasm import WasmMemoryManager
        mem = WasmMemoryManager(initial_pages=1)
        mem.write(0, b"hello")
        assert mem.read(0, 5) == b"hello"
    
    def test_memory_out_of_bounds(self):
        """Test out of bounds access."""
        from minxg.contracts.runtime.wasm import WasmMemoryManager
        mem = WasmMemoryManager(initial_pages=1)
        with pytest.raises(ValueError, match="out of bounds"):
            mem.read(0, 70000)


class TestWasmTableManager:
    """Test WASM table manager."""
    
    def test_table_set_get(self):
        """Test table set/get."""
        from minxg.contracts.runtime.wasm import WasmTableManager
        table = WasmTableManager(initial_size=5)
        table.set(0, "func_a")
        assert table.get(0) == "func_a"
    
    def test_table_grow(self):
        """Test table growth."""
        from minxg.contracts.runtime.wasm import WasmTableManager
        table = WasmTableManager(initial_size=2)
        assert table.size == 2
        table.grow(3)
        assert table.size == 5
    
    def test_table_out_of_bounds(self):
        """Test out of bounds access."""
        from minxg.contracts.runtime.wasm import WasmTableManager
        table = WasmTableManager(initial_size=2)
        with pytest.raises(ValueError, match="out of bounds"):
            table.get(5)


class TestWasmInstance:
    """Test WASM instance."""
    
    def test_instance_creation(self):
        """Test instance creation."""
        from minxg.contracts.runtime.wasm import WasmInstance, WasmModule
        module = WasmModule(name="test")
        instance = WasmInstance(module)
        assert instance.module.name == "test"
    
    def test_instance_initialize(self):
        """Test instance initialization."""
        from minxg.contracts.runtime.wasm import WasmInstance, WasmModule, WasmGlobal, WasmExport, WasmType
        module = WasmModule(name="test")
        module.globals = [WasmGlobal(type=WasmType(), mutable=False, init=42)]
        module.exports = [WasmExport(name="test_export", kind="func", index=0)]
        instance = WasmInstance(module)
        instance.initialize()
        assert len(instance.globals) == 1
        assert "test_export" in instance.exports


class TestWasmValidationResult:
    """Test WASM validation result."""
    
    def test_valid_result(self):
        """Test valid validation result."""
        from minxg.contracts.runtime.wasm import WasmValidationResult
        result = WasmValidationResult(valid=True)
        assert result.valid is True
        assert bool(result) is True
    
    def test_invalid_result(self):
        """Test invalid validation result."""
        from minxg.contracts.runtime.wasm import WasmValidationResult
        result = WasmValidationResult(valid=False, errors=["parse error"])
        assert result.valid is False
        assert bool(result) is False
        assert len(result.errors) == 1


class TestWasmHelpers:
    """Test WASM helper functions."""
    
    def test_validate_wat_valid(self):
        """Test valid WAT validation."""
        from minxg.contracts.runtime.wasm import validate_wat
        result = validate_wat("(module)")
        assert result.valid is True
    
    def test_validate_wat_invalid(self):
        """Test invalid WAT validation."""
        from minxg.contracts.runtime.wasm import validate_wat
        result = validate_wat("invalid wat!!!")
        assert result.valid is False
        assert len(result.errors) > 0
    
    def test_wat_to_hex(self):
        """Test WAT to hex conversion."""
        from minxg.contracts.runtime.wasm import wat_to_hex
        result = wat_to_hex("(module)")
        assert isinstance(result, str)
        assert len(result) > 0
