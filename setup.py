"""Map the MV-style checkout layout into one installable CLI namespace."""
from setuptools import find_packages, setup

roots = ("core", "nodes", "prompts", "profiles", "tools")
packages = ["vc_director"]
directories = {"vc_director": "."}
for root in roots:
    prefix = "vc_director." + root
    directories[prefix] = root
    packages.append(prefix)
    packages.extend(prefix + "." + name for name in find_packages(root))

setup(
    packages=packages,
    package_dir=directories,
    package_data={
        "vc_director": ["assets/emd/*.emd", "web/js/*.js", "workflows/*.json", "docs/implementation/mv-director-sources.json"],
        "vc_director.core.artifacts": ["schemas/*.json"],
        "vc_director.core.h3_contract": ["contracts/*.json"],
        "vc_director.prompts": ["*.txt"],
        "vc_director.profiles": ["runtime/*.json"],
        "vc_director.tools": ["workflow_templates/*.json"],
    },
    include_package_data=True,
)
