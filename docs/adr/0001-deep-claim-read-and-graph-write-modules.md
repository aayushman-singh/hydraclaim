# Use deep claim-read and graph-write modules

HydraClaim uses one claim-read module and one graph-write module. The claim-read module owns scoped reads, probes, route selection, and structured answers. The graph-write module owns identifiers, HydraDB query text, write order, and idempotency. This decision removes duplicated queries and private imports while keeping reconciliation rules separate from storage rules.

Raw HydraDB queries do not cross these module interfaces. Tests use a recording adapter at the existing HydraDB query seam.
