"""Filtered and grouped event queries from a simulation-thread callback.

Replace the client import with your vendored SDK namespace when packaging.
No imports of private provider modules, EA objects, files or network requests.
"""

from context_overlay_client import Client


def read_event_pages(sim_id):
    client = Client()
    with client.history("sim", str(sim_id), page_size=15, group_effects=True) as grouped:
        action_page = grouped.page
    with client.history("sim", str(sim_id), page_size=15, event_types=["game_event"],
                        fields=["skill.level", "life.age", "statistic.direct"]) as facts:
        fact_page = facts.page
    return {"available": True, "actions": action_page, "facts": fact_page}
