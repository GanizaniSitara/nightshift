# Plugins (and keeping them private)

Nightshift is split so the **public core ships only interfaces**; concrete implementations are
**plugins**. This is deliberate: a plugin that knows how to verify a *specific* project — its build
commands, its rubric, its paths — carries detail that should not sit in a public repo. Plugins can
live entirely outside this repository, in a private location, and still be loaded at runtime.

## What's core vs. what's a plugin

| Layer | Where it lives |
| --- | --- |
| Verifier contract + registry, orchestration records, worker contract, plugin loader | **public core** (`nightshift.*`) |
| Generic, non-sensitive reference verifiers (e.g. web, cli) | bundled in the public core as reference plugins |
| Project-specific verifiers, rubrics, run recipes, goal definitions | **private** — your own directory, not in this repo |

Right now most/all real plugins are expected to be private. That's fine — the public repo is the
framework; the private plugins are the working parts.

## How loading works

`nightshift.plugins.load_plugins()` gathers plugin directories, in order, from:

1. paths passed explicitly to `load_plugins(...)`
2. the `NIGHTSHIFT_PLUGINS` environment variable (os.pathsep-separated directories)
3. `config["plugin_paths"]`

Each directory's top-level `*.py` files and packages are imported. On import a plugin registers
itself (e.g. `registry.register(MyVerifier())` at module level) and/or defines a no-arg `register()`
function, which is called after import. Give plugin modules unique names.

## Writing a private plugin

See `examples/example_verifier_plugin.py`. Copy its shape into your private plugins directory:

```python
from nightshift.verifiers import registry
from nightshift.verifiers.base import Increment, Verdict, Verifier, VerificationResult

class MyProjectVerifier(Verifier):
    deliverable_type = "my-project-ui"
    def verify(self, increment, *, config):
        ...  # build/render + judge against the increment's rubric
        return VerificationResult(deliverable_type=self.deliverable_type, verdict=Verdict.PASS)

def register():
    registry.register(MyProjectVerifier())
```

Then point Nightshift at it:

```
set NIGHTSHIFT_PLUGINS=C:\path\to\your\private-plugins      (Windows)
export NIGHTSHIFT_PLUGINS=/path/to/your/private-plugins     (POSIX)
```

## Keeping private things private

- Don't put project names, paths, rubrics, or credentials in this public repo.
- Keep private plugins in their own directory/repo outside this tree, referenced via
  `NIGHTSHIFT_PLUGINS`.
- For convenience an in-tree `plugins.local/` directory is gitignored if you'd rather drop private
  plugins beside the code without committing them.
