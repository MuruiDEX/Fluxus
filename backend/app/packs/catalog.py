"""Declarative pack manifests — server-side only, never client-supplied.

A manifest is data: marketplace listing slugs plus an optional workflow
blueprint. Install always resolves those declarations against the global
catalog; arbitrary code, user manifests, and dynamic versions are not part of
this surface. Adding a pack means adding one immutable `PackSpec` here and its
catalog rows in the seed.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowTaskSpec:
    key: str
    title: str
    summary: str
    owner_slug: str
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowSpec:
    ref: str
    title: str
    goal: str
    tasks: tuple[WorkflowTaskSpec, ...]


@dataclass(frozen=True)
class PackSpec:
    slug: str
    version: str
    name: str
    description: str
    agent_slugs: tuple[str, ...]
    workflow: WorkflowSpec | None = None


FITNESS_PACK = PackSpec(
    slug="fitness-pack",
    version="1.0.0",
    name="Fitness Business Pack",
    description=(
        "Installs the AI Sales Manager and Customer Support AI, then creates a "
        "two-step client onboarding workflow ready for your team to run."
    ),
    agent_slugs=("sales-manager", "support-ai"),
    workflow=WorkflowSpec(
        ref="fitness-client-onboarding",
        title="Fitness Client Onboarding",
        goal="Qualify new leads and book their onboarding sessions.",
        tasks=(
            WorkflowTaskSpec(
                key="qualify",
                title="Qualify new lead",
                summary="Qualify the new lead and update the CRM.",
                owner_slug="sales-manager",
            ),
            WorkflowTaskSpec(
                key="book",
                title="Book onboarding session",
                summary="Book the onboarding session with the qualified lead.",
                owner_slug="support-ai",
                depends_on=("qualify",),
            ),
        ),
    ),
)

PACKS: dict[str, PackSpec] = {FITNESS_PACK.slug: FITNESS_PACK}


def get_pack(slug: str) -> PackSpec | None:
    """Known manifest by slug, or None (uniform 404 at the API boundary)."""
    return PACKS.get(slug)
