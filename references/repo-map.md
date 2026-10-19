# Repositories for the single issue owner

The launch payload, not this map, supplies owned paths and authority. Read each
relevant repository's current AGENTS.md/CLAUDE.md and toolchain instructions.
`work` handles the necessary repositories in one claim; chat reads them only.

| Repository | Role |
| --- | --- |
| Farm-Contract | Behavior contracts and network protocols. Draft routine contract updates when requirements are clear; ask about unresolved business semantics or explicitly required approvals. |
| common | Configuration source and its supported generators. Make authorized source changes and regenerate through the repository's toolchain. |
| farm-hive | Game server code and tests. Use scoped tests; request managed services only if required. |
| farmgui | Authored FairyGUI packages. Export the explicitly affected packages through `publish-ui`. |
| Farm-Client | Client code, necessary metadata, imported generated assets, tests and CI registration. |

Do not hand-edit generated protobuf/configuration or published UI bytes. Use the
source repository's documented generator/publisher and consumer importer; inspect
the resulting diff. Keep contract, UI and server work parallel only when their
inputs and write scopes are independent. Integrate dependencies before Client use.

A missing asset/access or unresolved business decision warrants a concrete question.
Ordinary implementation, metadata repair and necessary CI registration within the
issue scope do not require a separate repository owner or workflow stage.

Retired or operational repositories are not included merely because this map
mentions them. There is no permission to deploy, administer a host or modify live
services. Follow the current launch resource and publication boundaries.
