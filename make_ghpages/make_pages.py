"""Generate HTML pages for the OPTIMADE providers list."""
import datetime
import json
import os
import shutil
import string
import traceback
import urllib.request

from jinja2 import Environment, PackageLoader, select_autoescape
from optimade.models import IndexInfoResponse, LinksResponse
from optimade.validator import ImplementationValidator
from optimade.server.routers.utils import get_providers

# Subfolders
OUT_FOLDER = "out"
STATIC_FOLDER = "static"
HTML_FOLDER = (
    "providers"  # Name for subfolder where HTMLs for providers are going to be sitting
)
TEMPLATES_FOLDER = "templates"

# Absolute paths
pwd = os.path.split(os.path.abspath(__file__))[0]
STATIC_FOLDER_ABS = os.path.join(pwd, STATIC_FOLDER)


def extract_url(value):
    """To be used in the URLs of the sub databases.

    Indeed, sometimes its a AnyUrl, sometimes a Link(AnyUrl)
    """
    try:
        return value.href
    except AttributeError:
        return value


def get_index_metadb_data(base_url):
    """Return some info after inspecting the base_url of this index_metadb."""
    versions_to_test = ["v1", "v0.10", "v0"]

    provider_data = {}
    for version in versions_to_test:
        info_endpoint = f"{base_url}/{version}/info"
        try:
            with urllib.request.urlopen(info_endpoint) as url_response:
                response_content = url_response.read()
            provider_data["info_endpoint"] = info_endpoint
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            else:
                provider_data["state"] = "problem"
                provider_data[
                    "tooltip_lines"
                ] = "Generic error while fetching the data:\n{}".format(
                    traceback.format_exc()
                ).splitlines()
                provider_data["color"] = "light-red"
                return provider_data
    else:
        # Did not break: no version found
        provider_data["state"] = "not found"
        provider_data["tooltip_lines"] = [
            "I couldn't find the index meta-database, I tried the following versions: {}".format(
                ", ".join(versions_to_test)
            )
        ]
        provider_data["color"] = "light-red"
        return provider_data

    provider_data["state"] = "found"
    provider_data["color"] = "green"
    provider_data["version"] = version

    provider_data["default_subdb"] = None
    # Let's continue, it was found
    try:
        json_response = json.loads(response_content)
        IndexInfoResponse(**json_response)
    except Exception:
        # Adapt the badge info
        provider_data["state"] = "validation error"
        provider_data["color"] = "orange"
        provider_data[
            "tooltip_lines"
        ] = "Error while validating the Index MetaDB:\n{}".format(
            traceback.format_exc()
        ).splitlines()
        provider_data["version"] = version
    else:
        try:
            # For now I use this way of getting it
            provider_data["default_subdb"] = json_response["data"]["relationships"][
                "default"
            ]["data"]["id"]
        except Exception:
            # For now, whatever the error, I just ignore it
            pass

    links_endpoint = f"{base_url}/{version}/links"
    try:
        with urllib.request.urlopen(links_endpoint) as url_response:
            response_content = url_response.read()
    except urllib.error.HTTPError:
        provider_data["links_state"] = "problem"
        provider_data[
            "links_tooltip_lines"
        ] = "Generic error while fetching the /links endpoint:\n{}".format(
            traceback.format_exc()
        ).splitlines()
        provider_data["links_color"] = "light-red"
        return provider_data

    provider_data["links_endpoint"] = links_endpoint
    provider_data["links_state"] = "found"
    provider_data["links_color"] = "green"

    try:
        links_json_response = json.loads(response_content)
        LinksResponse(**links_json_response)
    except Exception:
        # Adapt the badge info
        provider_data["links_state"] = "validation error"
        provider_data["links_color"] = "orange"
        provider_data[
            "links_tooltip_lines"
        ] = "Error while validating the /links endpoint of the Index MetaDB:\n{}".format(
            traceback.format_exc()
        ).splitlines()
        return provider_data

    # We also filter out any non-child DB link type.
    all_linked_dbs = links_json_response["data"]
    subdbs = [
        subdb
        for subdb in all_linked_dbs
        if subdb["attributes"].get("link_type", "UNKNOWN") == "child"
    ]
    print(
        f"    [{len(all_linked_dbs)} links found, of which {len(subdbs)} child sub-dbs]"
    )

    # Order putting the default first, and then the rest in alphabetical order (by key)
    # Note that False gets before True.
    provider_data["subdbs"] = sorted(
        subdbs,
        key=lambda subdb: (subdb["id"] != provider_data["default_subdb"], subdb["id"]),
    )

    # Count the non-null ones
    non_null_subdbs = [
        subdb for subdb in provider_data["subdbs"] if subdb["attributes"]["base_url"]
    ]
    provider_data["num_non_null_subdbs"] = len(non_null_subdbs)

    provider_data["subdb_validation"] = {}
    for subdb in non_null_subdbs:
        url = subdb["attributes"]["base_url"]
        if (aggregate := subdb["attributes"].get("aggregate")) is None:
            aggregate = "ok"
        if aggregate != "ok":
            results = {}
            print(f"\t\tSkipping {subdb['id']} as aggregate is set to {aggregate}.")
            results["failure_count"] = 0
            results["failure_messages"] = []
            results["success_count"] = 0
            results["internal_failure_count"] = 0
            results["no_aggregate_reason"] = subdb["attributes"].get("no_aggregate_reason", "No reason given")

        else:
            results = validate_childdb(
                url.strip("/") + "/v1" if not url.endswith("/v1") else ""
            )
        results["aggregate"] = aggregate

        provider_data["subdb_validation"][url] = results
        provider_data["subdb_validation"][url]["valid"] = not results["failure_count"]
        # Count errors apart from internal errors
        provider_data["subdb_validation"][url]["total_count"] = (
            results["success_count"] + results["failure_count"]
        )
        try:
            ratio = results["success_count"] / (
                results["success_count"] + results["failure_count"]
            )
        except ZeroDivisionError:
            ratio = 0
        # Use the red/green values from the badge css
        ratio = 2 * (max(0.5, ratio) - 0.5)
        green = (77, 175, 74)
        red = (228, 26, 28)
        colour = list(green)

        for ind, channel in enumerate(colour):
            gradient = red[ind] - green[ind]
            colour[ind] += gradient * (1 - ratio)

        colour = [str(int(channel)) for channel in colour]
        provider_data["subdb_validation"][url][
            "_validator_results_colour"
        ] = f"rgb({','.join(colour)});"

        if provider_data["subdb_validation"][url].get("aggregate", "ok") != "ok":
            provider_data["subdb_validation"][url]["_validator_results_colour"] = "DarkGrey"

    return provider_data


def get_html_provider_fname(provider_id):
    """Return a valid html filename given the provider ID."""
    valid_characters = set(string.ascii_letters + string.digits + "_-")

    simple_string = "".join(c for c in provider_id if c in valid_characters)

    return "{}.html".format(simple_string)


def validate_childdb(url: str) -> dict:
    """Run the optimade-python-tools validator on the child database.

    Parameters:
        url: the URL of the child database.

    Returns:
        dictionary representation of the validation results.

    """
    import dataclasses
    from traceback import print_exc

    validator = ImplementationValidator(
        base_url=url, run_optional_tests=False, verbosity=1, fail_fast=False
    )

    try:
        validator.validate_implementation()
    except (Exception, SystemExit):
        print_exc()

    return dataclasses.asdict(validator.results)


def make_pages():
    """Create the rendered pages (index, and per-provider detail page)."""

    # Create output folder, copy static files
    if os.path.exists(OUT_FOLDER):
        shutil.rmtree(OUT_FOLDER)
    os.mkdir(OUT_FOLDER)
    os.mkdir(os.path.join(OUT_FOLDER, HTML_FOLDER))
    shutil.copytree(STATIC_FOLDER_ABS, os.path.join(OUT_FOLDER, STATIC_FOLDER))

    env = Environment(
        loader=PackageLoader("mod"),
        autoescape=select_autoescape(["html", "xml"]),
    )

    env.filters["extract_url"] = extract_url

    providers = get_providers()
    if not providers:
        raise RuntimeError("Unable to retrieve providers list.")

    last_check_time = datetime.datetime.utcnow().strftime("%A %B %d, %Y at %H:%M UTC")

    all_provider_data = []
    # Create HTML view for each provider
    for provider in providers:
        provider_data = {"id": provider["id"], "last_check_time": last_check_time}
        print("  - {}".format(provider["id"]))

        subpage = os.path.join(HTML_FOLDER, get_html_provider_fname(provider["id"]))
        subpage_abspath = os.path.join(OUT_FOLDER, subpage)

        provider_data["subpage"] = subpage
        provider_data["attributes"] = provider

        base_url = provider.get("base_url")

        if base_url is None:
            provider_data["index_metadb"] = {
                "state": "unspecified",
                "tooltip_lines": [
                    "The provider did not specify a base URL for the Index Meta-Database"
                ],
                "color": "dark-gray",
            }
        else:
            provider_data["index_metadb"] = {}
            try:
                index_metadb_data = get_index_metadb_data(base_url)
                provider_data["index_metadb"] = index_metadb_data
            except Exception:
                provider_data["index_metadb"] = {
                    "state": "unknown",
                    "tooltip_lines": "Generic error while fetching the data:\n{}".format(
                        traceback.format_exc()
                    ).splitlines(),
                    "color": "orange",
                }

        provider_data["title"] = f'{provider_data["attributes"].get("name")}: OPTIMADE provider dashboard'

        # Write provider html
        provider_html = env.get_template("singlepage.html").render(**provider_data)
        with open(subpage_abspath, "w") as f:
            f.write(provider_html)
        all_provider_data.append(provider_data)
        print("    - Page {} generated.".format(subpage))

    all_data = {}
    all_data["providers"] = sorted(
        all_provider_data, key=lambda provider: provider["id"]
    )
    all_data["globalsummary"] = {
        "with_base_url": len(all_data["providers"]),
        "num_sub_databases": sum(
            [
                provider_data.get("index_metadb", {}).get("num_non_null_subdbs", 0)
                for provider_data in all_provider_data
            ]
        ),
    }

    # Write main overview index
    print("[main index]")
    rendered = env.get_template("main_index.html").render(**all_data)
    outfile = os.path.join(OUT_FOLDER, "index.html")
    with open(outfile, "w") as f:
        f.write(rendered)
    print("  - index.html generated")


if __name__ == "__main__":
    make_pages()
