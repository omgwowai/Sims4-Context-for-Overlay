"""Verified EA localization fields, separate from gameplay interpretation.

Paths refer to exported tuning, not arbitrary strings in developer descriptions.
Variants are retained individually; a picker tooltip is not a resource name.
"""

FIELDS = {
    "buff": {"name": ("buff_name",), "description": ("buff_description",)},
    "relbit": {"name": ("display_name",), "description": ("bit_description",)},
    "statistic": {"name": ("stat_name",), "description": ("skill_description",)},
    "interaction": {"name": ("display_name_in_queue", "display_name")},
    "object_state": {"name": ("_display_data.instance_display_name",),
                     "description": ("_display_data.instance_display_description",)},
    "trait": {"name": ("display_name", "display_name_override"),
              "description": ("trait_description", "trait_origin_description")},
    "recipe": {"name": ("name", "multi_serving_name"),
               "description": ("recipe_description",), "tooltip": ("unavailable_tooltip",)},
    "mood": {"name": ("mood_names",), "description": ("descriptions",)},
    "aspiration": {"name": ("display_text", "_display_data.instance_display_name"),
                   "description": ("description_text", "_display_data.instance_display_description"),
                   "tooltip": ("_display_data.instance_display_tooltip",)},
    "aspiration_track": {"name": ("display_text",), "description": ("description_text",)},
    "career": {"name": ("career_display_name_override",)},
    "career_track": {"name": ("career_name", "career_name_gender_neutral"),
                     "description": ("career_description",)},
    "career_level": {"name": ("title",), "description": ("title_description",)},
}

# Only these direct fields have a safe runtime read. Composite/dynamic fields
# stay in the static reference catalog until their actual runtime accessor is known.
RUNTIME_FIELDS = {
    "mood": {"name": "mood_names", "description": "descriptions"},
    "buff": {"name": "buff_name", "description": "buff_description"},
    "relbit": {"name": "display_name", "description": "bit_description"},
    "statistic": {"name": "stat_name", "description": "skill_description"},
    "trait": {"name": "display_name", "description": "trait_description"},
    "recipe": {"name": "get_recipe_name", "description": "recipe_description", "tooltip": "unavailable_tooltip"},
    "object_state": {"name": "display_name", "description": "display_description"},
    "aspiration": {"name": "display_name", "description": "display_description", "tooltip": "display_tooltip"},
    "career_track": {"name": "career_name", "description": "career_description"},
    "career_level": {"name": "title", "description": "title_description"},
    "interaction": {"name": "get_name"},
}
