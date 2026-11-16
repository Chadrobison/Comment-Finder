"""Narrow metadata-routing backport for the pinned Instaloader 4.16 release.

Provenance: upstream PR https://github.com/instaloader/instaloader/pull/2795,
commit e3343a846cd7210902600b226dacf1b0586bd9e3. The upstream change routes
Profile and Post metadata through the existing documented web_api=True option.
That PR is not part of the 4.16 release. This adapter carries only those two
routing changes, not its new queries, login changes, or other behavior.

Both IDs already exist in 4.16's metadata methods. No query is constructed here,
no response triggers a fallback, and no failed request is retried. This adapter
is installed on one collection context; login and other contexts are unaffected.
Remove it when adopting an upstream release that includes the routing fix.
"""
from contextlib import nullcontext
from functools import wraps
import instaloader

_METADATA_PHASES = {
    '27937681195819736': 'profile metadata',
    '27128499623469141': 'post metadata',
}


def install_metadata_routes(context, phase_context=None):
    """Return whether the version-scoped adapter is active on this context."""
    if instaloader.__version__ != '4.16':
        return False
    if getattr(context, '_comment_finder_metadata_routes', False) is True:
        return True
    original = context.doc_id_graphql_query

    @wraps(original)
    def metadata_query(doc_id, variables, referer=None, web_api=False):
        phase = _METADATA_PHASES.get(doc_id)
        scope = phase_context(phase) if phase and phase_context else nullcontext()
        with scope:
            return original(doc_id, variables, referer=referer,
                            web_api=True if phase else web_api)

    context.doc_id_graphql_query = metadata_query
    context._comment_finder_metadata_routes = True
    return True
