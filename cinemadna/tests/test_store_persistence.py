"""AssetBrainStore 工厂资产库持久化 —— 跨生产复用地基（0成本，保原内存行为）。"""

from __future__ import annotations

from cinemadna.asset_brain.common.store import AssetBrainStore


class TestPersistence:
    def test_libraries_persist_across_instances(self, tmp_path):
        lib = tmp_path / "factory_lib"
        s1 = AssetBrainStore(library_dir=lib)
        s1.put_scene_atom("scene_x", {"asset_id": "scene_x", "tags": ["夜市"]})
        s1.put_prop("prop_bill", {"prop_id": "prop_bill", "state": "完整"})
        s1.put_character("char_a", {"character_id": "char_a"})
        # 新实例(同库目录)= 下一部生产 → 跨生产载入,reuse-first 才能命中
        s2 = AssetBrainStore(library_dir=lib)
        assert "scene_x" in s2.scene_atoms
        assert "prop_bill" in s2.prop_library
        assert "char_a" in s2.character_registry

    def test_counters_persist_monotonic(self, tmp_path):
        lib = tmp_path / "lib"
        s1 = AssetBrainStore(library_dir=lib)
        id1 = s1.next_id("wo_scene")
        s1.put_scene_atom("a", {})           # put 时持久化计数器
        s2 = AssetBrainStore(library_dir=lib)
        id2 = s2.next_id("wo_scene")
        assert id1 != id2                    # ID 跨生产单调不撞


class TestFileArchivalAndQualityGate:
    def test_asset_file_archived_to_factory_survives_wipe(self, tmp_path):
        # 1b：资产文件复制进工厂目录，路径改写 → bundle 删了也不断
        bundle = tmp_path / "bundle"; bundle.mkdir()
        img = bundle / "face.png"; img.write_bytes(b"\x89PNG" + b"0" * 900)
        lib = tmp_path / "factory"
        s1 = AssetBrainStore(library_dir=lib)
        s1.put_character("char_a", {"character_id": "char_a", "is_real_image": True,
                                    "face_image": str(img)})
        import shutil
        shutil.rmtree(bundle)                # 模拟 plan_d wipe bundle
        s2 = AssetBrainStore(library_dir=lib)
        rec = s2.character_registry["char_a"]
        from pathlib import Path
        assert Path(rec["face_image"]).is_file()          # 复用文件仍在(工厂目录)
        assert "factory" in rec["face_image"]

    def test_mock_asset_not_persisted(self, tmp_path):
        # 1d 质量门：mock/非真图资产不进持久库
        lib = tmp_path / "factory"
        s1 = AssetBrainStore(library_dir=lib)
        s1.put_prop("p_mock", {"prop_id": "p_mock", "mock_mode": True})
        s1.put_prop("p_unreal", {"prop_id": "p_unreal", "is_real_image": False})
        s1.put_prop("p_good", {"prop_id": "p_good", "is_real_image": True})
        s2 = AssetBrainStore(library_dir=lib)
        assert "p_good" in s2.prop_library
        assert "p_mock" not in s2.prop_library and "p_unreal" not in s2.prop_library

    def test_gate_failed_asset_not_persisted(self, tmp_path):
        lib = tmp_path / "factory"
        AssetBrainStore(library_dir=lib).put_scene_atom(
            "s_bad", {"asset_id": "s_bad", "gate_passed": False})
        assert "s_bad" not in AssetBrainStore(library_dir=lib).scene_atoms


class TestBackwardCompat:
    def test_default_is_in_memory_unchanged(self):
        # 默认 AssetBrainStore() 无 library_dir = 原纯内存行为不变（保原功能）
        s = AssetBrainStore()
        assert s.library_dir is None
        s.put_scene_atom("x", {})
        assert "x" not in AssetBrainStore().scene_atoms   # 内存不跨实例共享

    def test_workorders_not_persisted(self, tmp_path):
        # 临时数据(workorder/gate/backflow)不落盘，只库持久化
        from cinemadna.asset_brain.common.store import AssetBrainStore
        lib = tmp_path / "lib"
        s1 = AssetBrainStore(library_dir=lib)
        s1.put_gate_report({"gate_id": "g1", "passed": True})
        s2 = AssetBrainStore(library_dir=lib)
        assert "g1" not in s2.gate_reports    # gate 是每 run 临时
