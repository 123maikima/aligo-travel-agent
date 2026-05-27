"""
记忆更新器 MemoryUpdater
职责：根据 Agent 执行结果更新长期记忆
"""
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)


class MemoryUpdater:
    """根据 Agent 执行结果更新长期记忆"""

    @staticmethod
    def update(memory_manager, results: List[Dict]):
        for result in results:
            agent_name = result["agent_name"]
            data = result["result"].get("data", {})
            if agent_name == "preference" and isinstance(data, dict):
                MemoryUpdater._update_preferences(memory_manager, data)
            elif agent_name == "itinerary_planning" and isinstance(data, dict):
                MemoryUpdater._update_trip_history(memory_manager, data, results)

    @staticmethod
    def _update_preferences(memory_manager, data: Dict):
        preferences_data = data.get("preferences", {})
        if isinstance(preferences_data, list):
            for pref_item in preferences_data:
                if not isinstance(pref_item, dict):
                    continue
                pref_type = pref_item.get("type")
                pref_value = pref_item.get("value")
                pref_action = pref_item.get("action", "replace")
                if not pref_type or not pref_value:
                    continue

                if pref_action == "append":
                    current_prefs = memory_manager.long_term.get_preference()
                    existing_value = current_prefs.get(pref_type)
                    if isinstance(existing_value, list):
                        if pref_value not in existing_value:
                            existing_value.append(pref_value)
                        memory_manager.long_term.save_preference(pref_type, existing_value)
                    else:
                        new_list = [existing_value, pref_value] if existing_value else [pref_value]
                        memory_manager.long_term.save_preference(pref_type, new_list)
                else:
                    memory_manager.long_term.save_preference(pref_type, pref_value)
        elif isinstance(preferences_data, dict):
            for pref_type, value in preferences_data.items():
                if value and pref_type not in ("has_preferences", "error"):
                    memory_manager.long_term.save_preference(pref_type, value)

    @staticmethod
    def _update_trip_history(memory_manager, data: Dict, all_results: List[Dict]):
        itinerary = data.get("itinerary", {})
        if not itinerary:
            return

        event_data = {}
        for r in all_results:
            if r["agent_name"] == "event_collection":
                event_data = r["result"].get("data", {})
                break

        destination = event_data.get("destination")
        if destination:
            memory_manager.long_term.save_trip_history({
                "origin": event_data.get("origin"),
                "destination": destination,
                "start_date": event_data.get("start_date"),
                "end_date": event_data.get("end_date"),
                "purpose": event_data.get("trip_purpose", "旅游")
            })
