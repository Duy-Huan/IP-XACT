"""Bounded structural hierarchy and explicit view selection, without bus inference."""

import re

from .domain import ContractError
from .ipxact import PREFIX, identity, text


def reference(node):
    if node is None:
        raise ContractError("E_HIERARCHY_REFERENCE missing document reference")
    return ":".join(node.get(k, "") for k in ("vendor", "library", "name", "version"))


def document(catalog, node, kind):
    vlnv = reference(node)
    result = catalog.get(vlnv)
    if result is None or result.tag != PREFIX + kind:
        raise ContractError(f"E_HIERARCHY_REFERENCE unresolved {kind}: {vlnv}")
    return result


def configured_design(root, catalog):
    if root.tag == PREFIX + "design":
        return root, {}
    if root.tag != PREFIX + "designConfiguration":
        raise ContractError("E_HIERARCHY_REFERENCE expected design/configuration")
    design = document(catalog, root.find(PREFIX + "designRef"), "design")
    selections = {}
    names = {
        text(i, "instanceName")
        for i in design.findall(f"{PREFIX}componentInstances/{PREFIX}componentInstance")
    }
    for config in root.findall(PREFIX + "viewConfiguration"):
        name = text(config, "instanceName")
        if name not in names or name in selections:
            raise ContractError(f"E_HIERARCHY_VIEW invalid configuration instance {name}")
        view = config.find(PREFIX + "view")
        if len(view):
            raise ContractError("UNSUPPORTED configured-view parameters")
        selections[name] = view.get("viewRef")
    return design, selections


def leaves(root, catalog):
    result = []

    def descend(root, path, active):
        design, selections = configured_design(root, catalog)
        vlnv = identity(design)
        context = (vlnv, tuple(sorted(selections.items())))
        if context in active:
            raise ContractError(
                "E_HIERARCHY_CYCLE " + " -> ".join(c[0] for c in (*active, context))
            )
        if len(active) >= 32:
            raise ContractError("E_HIERARCHY_LIMIT exceeds 32 design levels")
        items = design.findall(f"{PREFIX}componentInstances/{PREFIX}componentInstance")
        if not items:
            raise ContractError("design has no component instances")
        seen = set()
        for item in items:
            name = text(item, "instanceName")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or name in seen:
                raise ContractError(f"duplicate/unsafe instance name {name}")
            seen.add(name)
            full_path = (*path, name)
            ref = item.find(PREFIX + "componentRef")
            component = document(catalog, ref, "component")
            model = component.find(PREFIX + "model")
            if model is None:
                if name in selections:
                    raise ContractError(f"E_HIERARCHY_VIEW leaf {name} has no model views")
                result.append((full_path, component, ref))
                if len(result) > 4096:
                    raise ContractError("E_HIERARCHY_LIMIT exceeds 4096 leaves")
                continue
            if (
                component.find(PREFIX + "memoryMaps") is not None
                or component.find(PREFIX + "parameters") is not None
            ):
                raise ContractError("UNSUPPORTED hierarchical wrapper memoryMaps/parameters")
            if len(ref):
                raise ContractError("UNSUPPORTED hierarchical instance parameter propagation")
            views = model.findall(f"{PREFIX}views/{PREFIX}view")
            selected = selections.get(name)
            if selected is None:
                if len(views) != 1:
                    raise ContractError(f"E_HIERARCHY_VIEW ambiguous view for {name}")
                view = views[0]
            else:
                matches = [v for v in views if text(v, "name") == selected]
                if len(matches) != 1:
                    raise ContractError(f"E_HIERARCHY_VIEW unknown view {selected} for {name}")
                view = matches[0]
            links = [
                (tag, view.findtext(PREFIX + tag + "Ref"))
                for tag in ("designInstantiation", "designConfigurationInstantiation")
            ]
            links = [(tag, value) for tag, value in links if value is not None]
            if len(links) != 1:
                raise ContractError("E_HIERARCHY_VIEW expected one structural instantiation")
            tag, value = links[0]
            candidates = [
                i
                for i in model.findall(f"{PREFIX}instantiations/{PREFIX}{tag}")
                if text(i, "name") == value
            ]
            if len(candidates) != 1:
                raise ContractError("E_HIERARCHY_REFERENCE ambiguous/missing instantiation")
            target_kind = "design" if tag == "designInstantiation" else "designConfiguration"
            child = document(catalog, candidates[0].find(PREFIX + target_kind + "Ref"), target_kind)
            descend(child, full_path, (*active, context))

    descend(root, (), ())
    return result
