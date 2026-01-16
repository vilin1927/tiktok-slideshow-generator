"""
Preset API Routes

Endpoints for listing and selecting text presets.
"""

from flask import Blueprint, jsonify

from presets import list_all_presets, get_preset, get_gemini_option


preset_bp = Blueprint('presets', __name__, url_prefix='/api/presets')


@preset_bp.route('', methods=['GET'])
def list_presets():
    """
    List all available text presets.

    Returns:
        JSON with list of presets including:
        - Gemini option (auto text)
        - 9 manual presets (3 fonts × 3 effects)
    """
    # Start with Gemini option as default
    presets = [get_gemini_option()]

    # Add all manual presets
    presets.extend(list_all_presets())

    return jsonify({
        'presets': presets,
        'default': 'gemini'
    })


@preset_bp.route('/<preset_id>', methods=['GET'])
def get_single_preset(preset_id: str):
    """
    Get details for a specific preset.

    Args:
        preset_id: Preset ID (e.g., 'classic_shadow' or 'gemini')

    Returns:
        JSON with preset details
    """
    if preset_id == 'gemini':
        return jsonify(get_gemini_option())

    preset = get_preset(preset_id)
    if preset is None:
        return jsonify({'error': f'Preset not found: {preset_id}'}), 404

    return jsonify({
        'id': preset.id,
        'display_name': preset.display_name,
        'font': {
            'name': preset.font.name,
            'file': preset.font.file,
            'style': preset.font.style
        },
        'effect': {
            'name': preset.effect.name,
            'type': preset.effect.type
        }
    })
