"""Versioned display conventions for pre-appearance-map Project bundles.

This is a viewer compatibility policy, not evidence of original visual fidelity.
It never edits sealed documents, infers rules or changes compilation acceptance.
Only the old, finite semantic vocabulary is supported; unknown states still fail.
"""
from copy import deepcopy

LEGACY_PROFILE = 'legacy-semantic-markers/1'

_STYLES = {
    'empty': {'color': [.40, .57, .76, 1], 'opacity': .12, 'scale': [.9]*3, 'text': ''},
    'positive': {'color': [.27, .55, 1, 1], 'opacity': .20, 'scale': [.9]*3,
                 'marker': {'kind': 'cross', 'color': [.27, .55, 1, 1], 'size': .62}, 'text': ''},
    'negative': {'color': [1, .29, .29, 1], 'opacity': .20, 'scale': [.9]*3,
                 'marker': {'kind': 'ring', 'color': [1, .29, .29, 1], 'size': .62}, 'text': ''},
    'body': {'color': [.22, .7, .49, 1], 'opacity': .92, 'scale': [.76]*3, 'text': ''},
    'head': {'color': [.32, .75, 1, 1], 'opacity': 1, 'scale': [.82]*3,
             'marker': {'kind': 'sphere', 'color': [.85, .96, 1, 1], 'size': .28}, 'text': ''},
    'food': {'color': [1, .38, .22, 1], 'opacity': .12, 'scale': [.9]*3,
             'marker': {'kind': 'sphere', 'color': [1, .38, .22, 1], 'size': .60}, 'text': ''},
}


def legacy_style(variant):
    """Return a copy; never apply these conventions over explicit appearance."""
    return deepcopy(_STYLES.get(variant))
