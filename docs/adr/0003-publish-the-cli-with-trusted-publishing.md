# Publish the CLI with trusted publishing

HydraClaim publishes one `hydraclaim` command to the Python Package Index. Ten subcommands call the same command implementations as the Python module forms. GitHub Actions builds one artifact set and publishes it from a version tag through OpenID Connect trusted publishing.

This decision avoids stored release tokens and prevents local builds from differing from published files.
