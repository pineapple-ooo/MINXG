"""AgentHarness Julia warmup bridge — precompiles the Julia runtime on first use.

This file is intentionally minimal; it exists solely to trigger package
precompilation so that the first real bridge invocation is fast.
"""
println("warmup-ok")
