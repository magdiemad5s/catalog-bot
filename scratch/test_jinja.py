import jinja2

env = jinja2.Environment(loader=jinja2.FileSystemLoader('templates'))
try:
    template = env.get_template('antiraid.html')
    res = template.render(
        active_page="antiraid",
        config={
            "guild_id": 123,
            "enabled": False,
            "account_age_min_days": 7,
            "join_rate_count": 5,
            "join_rate_window_seconds": 10,
            "penalty_action": "kick",
            "mute_duration_minutes": 60,
            "alert_channel_id": None,
            "quarantine_role_id": None
        },
        channels=[{"id": 1, "name": "general"}],
        roles=[{"id": 2, "name": "role1"}]
    )
    print("ANTIRAID OK")
except Exception as e:
    import traceback
    traceback.print_exc()

try:
    template2 = env.get_template('filter.html')
    res2 = template2.render(
        active_page="filter",
        config={"guild_id": 123, "enabled": False, "active_profile_id": None, "threshold": 3, "mod_channel_id": None},
        profiles=[{"id": 1, "name": "Test", "word_list": ["a"]}],
        logs=[],
        channels=[{"id": 1, "name": "test"}]
    )
    print("FILTER OK")
except Exception as e:
    import traceback
    traceback.print_exc()
