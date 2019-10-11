import collections
from itertools import chain
import pprint
import re

from .development.base_component import Component
from .dependencies import Input, Output, State
from . import exceptions
from ._utils import create_callback_id, patch_collections_abc

# py2/3 json.dumps-compatible strings - these are equivalent in py3, not in py2
_strings = (type(u""), type(""))


def validate_callback(app, layout, output, inputs, state):
    is_multi = isinstance(output, (list, tuple))
    validate_ids = not app.config.suppress_callback_exceptions

    if layout is None and validate_ids:
        # Without a layout, we can't do validation on the IDs and
        # properties of the elements in the callback.
        raise exceptions.LayoutIsNotDefined(
            """
            Attempting to assign a callback to the application but
            the `layout` property has not been assigned.
            Assign the `layout` property before assigning callbacks.
            Alternatively, suppress this warning by setting
            `suppress_callback_exceptions=True`
            """
        )

    outputs = output if is_multi else [output]
    for args, cls in [(outputs, Output), (inputs, Input), (state, State)]:
        validate_callback_args(args, cls, layout, validate_ids)

    if state and not inputs:
        raise exceptions.MissingInputsException(
            """
            This callback has {} `State` element{} but no `Input` elements.

            Without `Input` elements, this callback will never get called.

            (Subscribing to Input components will cause the
            callback to be called whenever their values change.)
            """.format(
                len(state), "s" if len(state) > 1 else ""
            )
        )

    for i in inputs:
        bad = None
        if is_multi:
            for o in output:
                if o == i:
                    bad = o
        else:
            if output == i:
                bad = output
        if bad:
            raise exceptions.SameInputOutputException(
                "Same output and input: {}".format(bad)
            )

    if is_multi:
        if len(set(output)) != len(output):
            raise exceptions.DuplicateCallbackOutput(
                "Same output was used more than once in a "
                "multi output callback!\nDuplicates:\n{}".format(
                    ",\n".join(str(x) for x in output if output.count(x) > 1)
                )
            )

    callback_id = create_callback_id(output)

    callbacks = set(
        chain(
            *(
                x[2:-2].split("...") if x.startswith("..") else [x]
                for x in app.callback_map
            )
        )
    )

    if is_multi:
        dups = callbacks.intersection(str(y) for y in output)
        if dups:
            raise exceptions.DuplicateCallbackOutput(
                """
                Multi output {} contains an `Output` object
                that was already assigned.
                Duplicates:
                {}
                """.format(
                    callback_id, pprint.pformat(dups)
                )
            )
    else:
        if callback_id in callbacks:
            raise exceptions.DuplicateCallbackOutput(
                """
                You have already assigned a callback to the output
                with ID "{}" and property "{}". An output can only have
                a single callback function. Try combining your inputs and
                callback functions together into one function.
                """.format(
                    output.component_id, output.component_property
                )
            )


def validate_callback_args(args, cls, layout, validate_ids):
    name = cls.__name__
    if not isinstance(args, (list, tuple)):
        raise exceptions.IncorrectTypeException(
            """
            The {} argument `{}` must be a list or tuple of
            `dash.dependencies.{}`s.
            """.format(
                name.lower(), str(args), name
            )
        )

    for arg in args:
        if not isinstance(arg, cls):
            raise exceptions.IncorrectTypeException(
                """
                The {} argument `{}` must be of type `dash.dependencies.{}`.
                """.format(
                    name.lower(), str(arg), name
                )
            )

        if not isinstance(arg.component_id, _strings):
            raise exceptions.IncorrectTypeException(
                """
                component_id must be a string or dict, found {!r}
                """.format(
                    arg.component_id
                )
            )

        if not isinstance(arg.component_property, _strings):
            raise exceptions.IncorrectTypeException(
                """
                component_property must be a string, found {!r}
                """.format(
                    arg.component_property
                )
            )

        invalid_characters = ["."]
        if any(x in arg.component_id for x in invalid_characters):
            raise exceptions.InvalidComponentIdError(
                """
                The element `{}` contains {} in its ID.
                Periods are not allowed in IDs.
                """.format(
                    arg.component_id, invalid_characters
                )
            )

        if validate_ids:
            top_id = getattr(layout, "id", None)
            arg_id = arg.component_id
            arg_prop = getattr(arg, "component_property", None)
            if arg_id not in layout and arg_id != top_id:
                raise exceptions.NonExistentIdException(
                    """
                    Attempting to assign a callback to the component with
                    id "{}" but no components with that id exist in the layout.

                    Here is a list of IDs in layout:
                    {}

                    If you are assigning callbacks to components that are
                    generated by other callbacks (and therefore not in the
                    initial layout), you can suppress this exception by setting
                    `suppress_callback_exceptions=True`.
                    """.format(
                        arg_id, [k for k in layout] + ([top_id] if top_id else [])
                    )
                )

            component = layout if top_id == arg_id else layout[arg_id]

            if (
                arg_prop
                and arg_prop not in component.available_properties
                and not any(
                    arg_prop.startswith(w)
                    for w in component.available_wildcard_properties
                )
            ):
                raise exceptions.NonExistentPropException(
                    """
                    Attempting to assign a callback with the property "{0}"
                    but component "{1}" doesn't have "{0}" as a property.

                    Here are the available properties in "{1}":
                    {2}
                    """.format(
                        arg_prop, arg_id, component.available_properties
                    )
                )

            if hasattr(arg, "component_event"):
                raise exceptions.NonExistentEventException(
                    """
                    Events have been removed.
                    Use the associated property instead.
                    """
                )


def validate_multi_return(output, output_value, callback_id):
    if not isinstance(output_value, (list, tuple)):
        raise exceptions.InvalidCallbackReturnValue(
            """
            The callback {} is a multi-output.
            Expected the output type to be a list or tuple but got:
            {}.
            """.format(
                callback_id, repr(output_value)
            )
        )

    if not len(output_value) == len(output):
        raise exceptions.InvalidCallbackReturnValue(
            """
            Invalid number of output values for {}.
            Expected {}, got {}
            """.format(
                callback_id, len(output), len(output_value)
            )
        )


def fail_callback_output(output_value, output):
    valid = _strings + (dict, int, float, type(None), Component)

    def _raise_invalid(bad_val, outer_val, path, index=None, toplevel=False):
        bad_type = type(bad_val).__name__
        outer_id = (
            "(id={:s})".format(outer_val.id) if getattr(outer_val, "id", False) else ""
        )
        outer_type = type(outer_val).__name__
        if toplevel:
            location = """
            The value in question is either the only value returned,
            or is in the top level of the returned list,
            """
        else:
            index_string = "[*]" if index is None else "[{:d}]".format(index)
            location = """
            The value in question is located at
            {} {} {}
            {},
            """.format(
                index_string, outer_type, outer_id, path
            )

        raise exceptions.InvalidCallbackReturnValue(
            """
            The callback for `{output}`
            returned a {object:s} having type `{type}`
            which is not JSON serializable.

            {location}
            and has string representation
            `{bad_val}`

            In general, Dash properties can only be
            dash components, strings, dictionaries, numbers, None,
            or lists of those.
            """.format(
                output=repr(output),
                object="tree with one value" if not toplevel else "value",
                type=bad_type,
                location=location,
                bad_val=bad_val,
            )
        )

    def _value_is_valid(val):
        return isinstance(val, valid)

    def _validate_value(val, index=None):
        # val is a Component
        if isinstance(val, Component):
            # pylint: disable=protected-access
            for p, j in val._traverse_with_paths():
                # check each component value in the tree
                if not _value_is_valid(j):
                    _raise_invalid(bad_val=j, outer_val=val, path=p, index=index)

                # Children that are not of type Component or
                # list/tuple not returned by traverse
                child = getattr(j, "children", None)
                if not isinstance(child, (tuple, collections.MutableSequence)):
                    if child and not _value_is_valid(child):
                        _raise_invalid(
                            bad_val=child,
                            outer_val=val,
                            path=p + "\n" + "[*] " + type(child).__name__,
                            index=index,
                        )

            # Also check the child of val, as it will not be returned
            child = getattr(val, "children", None)
            if not isinstance(child, (tuple, collections.MutableSequence)):
                if child and not _value_is_valid(child):
                    _raise_invalid(
                        bad_val=child,
                        outer_val=val,
                        path=type(child).__name__,
                        index=index,
                    )

        # val is not a Component, but is at the top level of tree
        elif not _value_is_valid(val):
            _raise_invalid(
                bad_val=val,
                outer_val=type(val).__name__,
                path="",
                index=index,
                toplevel=True,
            )

    if isinstance(output_value, list):
        for i, val in enumerate(output_value):
            _validate_value(val, index=i)
    else:
        _validate_value(output_value)

    # if we got this far, raise a generic JSON error
    raise exceptions.InvalidCallbackReturnValue(
        """
        The callback for property `{property:s}` of component `{id:s}`
        returned a value which is not JSON serializable.

        In general, Dash properties can only be dash components, strings,
        dictionaries, numbers, None, or lists of those.
        """.format(
            property=output.component_property, id=output.component_id
        )
    )


def check_obsolete(kwargs):
    for key in kwargs:
        if key in ["components_cache_max_age", "static_folder"]:
            raise exceptions.ObsoleteKwargException(
                """
                {} is no longer a valid keyword argument in Dash since v1.0.
                See https://dash.plot.ly for details.
                """.format(
                    key
                )
            )
        # any other kwarg mimic the built-in exception
        raise TypeError("Dash() got an unexpected keyword argument '" + key + "'")


def validate_js_path(registered_paths, package_name, path_in_package_dist):
    if package_name not in registered_paths:
        raise exceptions.DependencyException(
            """
            Error loading dependency. "{}" is not a registered library.
            Registered libraries are:
            {}
            """.format(
                package_name, list(registered_paths.keys())
            )
        )

    if path_in_package_dist not in registered_paths[package_name]:
        raise exceptions.DependencyException(
            """
            "{}" is registered but the path requested is not valid.
            The path requested: "{}"
            List of registered paths: {}
            """.format(
                package_name, path_in_package_dist, registered_paths
            )
        )


def validate_index(name, checks, index):
    missing = [i for check, i in checks if not re.compile(check).search(index)]
    if missing:
        plural = "s" if len(missing) > 1 else ""
        raise exceptions.InvalidIndexException(
            "Missing item{pl} {items} in {name}.".format(
                items=", ".join(missing), pl=plural, name=name
            )
        )


def validate_layout_type(value):
    if not isinstance(value, (Component, patch_collections_abc("Callable"))):
        raise exceptions.NoLayoutException(
            "Layout must be a dash component "
            "or a function that returns a dash component."
        )


def validate_layout(layout, layout_value):
    if layout is None:
        raise exceptions.NoLayoutException(
            """
            The layout was `None` at the time that `run_server` was called.
            Make sure to set the `layout` attribute of your application
            before running the server.
            """
        )

    layout_id = getattr(layout_value, "id", None)

    component_ids = {layout_id} if layout_id else set()
    # pylint: disable=protected-access
    for component in layout_value._traverse():
        component_id = getattr(component, "id", None)
        if component_id and component_id in component_ids:
            raise exceptions.DuplicateIdError(
                """
                Duplicate component id found in the initial layout: `{}`
                """.format(
                    component_id
                )
            )
        component_ids.add(component_id)