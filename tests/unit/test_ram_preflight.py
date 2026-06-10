"""Pre-load RAM preflight: available-memory detection and KV estimation.

Regression context: the macOS path previously compared the load estimate
against `hw.memsize` (total physical RAM, not available), and the estimate
ignored ctx_len entirely. Inspired by jundot/omlx#1763 (KV preflight
accuracy) and their macOS 27 HOST_VM_INFO64 breakage — we parse `vm_stat`
text instead of Mach structs.
"""

import json
import platform
from unittest import mock

from mycellm.inference.manager import (
    _available_ram_bytes,
    _estimate_kv_bytes,
    _parse_vm_stat,
)

VM_STAT_SAMPLE = """\
Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                              102400.
Pages active:                           1843200.
Pages inactive:                          512000.
Pages speculative:                        25600.
Pages throttled:                              0.
Pages wired down:                        409600.
Pages purgeable:                          51200.
"Translation faults":                 123456789.
"""


class TestParseVmStat:
    def test_sums_free_inactive_purgeable(self):
        # (102400 + 512000 + 51200) pages * 16384 bytes
        assert _parse_vm_stat(VM_STAT_SAMPLE) == 665600 * 16384

    def test_active_and_wired_excluded(self):
        result = _parse_vm_stat(VM_STAT_SAMPLE)
        assert result < (665600 + 1843200) * 16384

    def test_page_size_parsed_from_header(self):
        sample = VM_STAT_SAMPLE.replace("16384", "4096")
        assert _parse_vm_stat(sample) == 665600 * 4096

    def test_garbage_input_returns_zero(self):
        assert _parse_vm_stat("not vm_stat output") == 0


class TestAvailableRam:
    def test_linux_reads_memavailable(self):
        if platform.system() != "Linux":
            return
        avail = _available_ram_bytes()
        assert avail > 0  # real /proc/meminfo on CI/dev boxes

    def test_darwin_uses_vm_stat(self):
        completed = mock.Mock(returncode=0, stdout=VM_STAT_SAMPLE)
        with mock.patch("platform.system", return_value="Darwin"), \
             mock.patch("subprocess.run", return_value=completed):
            assert _available_ram_bytes() == 665600 * 16384

    def test_failure_returns_zero_not_raise(self):
        with mock.patch("platform.system", return_value="Darwin"), \
             mock.patch("subprocess.run", side_effect=OSError("no vm_stat")):
            assert _available_ram_bytes() == 0


class TestEstimateKvBytes:
    def test_mlx_config_geometry(self, tmp_path):
        # Qwen2.5-7B-like geometry: 28 layers, GQA 4 kv heads, head_dim 128.
        (tmp_path / "config.json").write_text(json.dumps({
            "num_hidden_layers": 28,
            "num_attention_heads": 28,
            "num_key_value_heads": 4,
            "hidden_size": 3584,
        }))
        ctx = 8192
        expected = 2 * 28 * 4 * 128 * 2 * ctx  # K+V × layers × kv × dim × fp16
        assert _estimate_kv_bytes(str(tmp_path), ctx, 0) == expected

    def test_explicit_head_dim_wins(self, tmp_path):
        (tmp_path / "config.json").write_text(json.dumps({
            "num_hidden_layers": 10,
            "num_attention_heads": 8,
            "num_key_value_heads": 2,
            "head_dim": 64,
            "hidden_size": 99999,  # would give a wrong head_dim if used
        }))
        assert _estimate_kv_bytes(str(tmp_path), 100, 0) == 2 * 10 * 2 * 64 * 2 * 100

    def test_kv_scales_with_ctx(self, tmp_path):
        (tmp_path / "config.json").write_text(json.dumps({
            "num_hidden_layers": 28, "num_attention_heads": 28,
            "num_key_value_heads": 4, "hidden_size": 3584,
        }))
        small = _estimate_kv_bytes(str(tmp_path), 4096, 0)
        large = _estimate_kv_bytes(str(tmp_path), 32768, 0)
        assert large == small * 8

    def test_gguf_falls_back_to_size_heuristic(self, tmp_path):
        # No config.json (GGUF single file): ~100KB/token for a 4.5GB model.
        file_size = 4_500_000_000
        est = _estimate_kv_bytes(str(tmp_path / "model.gguf"), 32768, file_size)
        assert est == (file_size // 45_000) * 32768
        assert 2 * 1024**3 < est < 5 * 1024**3  # sane: a few GB at 32k ctx

    def test_malformed_config_falls_back(self, tmp_path):
        (tmp_path / "config.json").write_text("{not json")
        assert _estimate_kv_bytes(str(tmp_path), 1000, 45_000_000) == 1_000_000
