import jobseeker.scrapers_discovery as discovery


def test_merge_into_config_adds_discovered_entries(tmp_path):
    discovery.DISCOVERED_PATH = tmp_path / "discovered.json"
    discovery.save_discovered({
        "greenhouse": ["acme", "newco"],
        "lever": ["beta", "nextco"],
        "kerala_sites": ["https://alpha.example", "https://beta.example"],
    })

    cfg = {
        "sources": {
            "greenhouse": {"enabled": True, "companies": ["acme"]},
            "lever": {"enabled": True, "companies": ["beta"]},
            "kerala": {"enabled": True, "companies": [{"name": "Existing", "website": "https://existing.example", "city": "Kochi"}]},
        }
    }

    merged = discovery.merge_into_config(cfg)

    assert "newco" in merged["sources"]["greenhouse"]["companies"]
    assert "nextco" in merged["sources"]["lever"]["companies"]
    assert any(item["website"] == "https://alpha.example" for item in merged["sources"]["kerala"]["companies"])
