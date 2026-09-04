"""Tk rendering adapters for tagged UI messages; no global Tk monkey-patching."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk, filedialog

from liquid_nitrogen_tank_i18n import Message, render


def state_for(widget):
    return getattr(widget._root(), "language_state", None)


def display(widget, value):
    state = state_for(widget)
    return render(value, state.language if state else "zh")


def register(widget):
    state = state_for(widget)
    if state:
        state.widgets.add(widget)


def _file_dialog(function, **options):
    parent = options.get('parent')
    if parent is not None:
        if 'title' in options:
            options['title'] = display(parent, options['title'])
        if 'initialfile' in options:
            options['initialfile'] = display(parent, options['initialfile'])
        if 'filetypes' in options:
            options['filetypes'] = tuple((display(parent, name), pattern) for name, pattern in options['filetypes'])
    return function(**options)


def askopenfilename(**options):
    return _file_dialog(filedialog.askopenfilename, **options)


def asksaveasfilename(**options):
    return _file_dialog(filedialog.asksaveasfilename, **options)


class MessageVar(tk.StringVar):
    def __init__(self, master=None, value=None, name=None):
        self.message = value
        super().__init__(master, value, name)

    def set(self, value):
        self.message = value
        return super().set(value)

    def source(self):
        value = self.get()
        return self.message if str(self.message) == value else value


class TextMixin:
    def __init__(self, master=None, **kwargs):
        self._source_text = kwargs.pop("text", "")
        self._source_variable = kwargs.pop("textvariable", None)
        self._text_trace = None
        super().__init__(master, **kwargs)
        if isinstance(self._source_variable, tk.Variable):
            self._text_trace = self._source_variable.trace_add("write", lambda *_: self._apply_language())
        self.bind("<Destroy>", self._release_text_trace, add="+")
        register(self)
        self._apply_language()

    def _release_text_trace(self, event):
        if event.widget is self and self._text_trace:
            try:
                self._source_variable.trace_remove("write", self._text_trace)
            except tk.TclError:
                pass
            self._text_trace = None

    def _apply_language(self):
        source = self._source_text
        if isinstance(self._source_variable, MessageVar):
            source = self._source_variable.source()
        elif isinstance(self._source_variable, tk.Variable):
            source = self._source_variable.get()
        super().configure(text=display(self, source))

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, str):
            return super().configure(cnf, **kwargs)
        options = dict(cnf or {}, **kwargs)
        if "text" in options:
            self._source_text = options["text"]
            options["text"] = display(self, self._source_text)
        return super().configure(**options)

    config = configure


class Label(TextMixin, tk.Label):
    pass


class TtkLabel(TextMixin, ttk.Label):
    pass


class Button(TextMixin, tk.Button):
    pass


class TtkButton(TextMixin, ttk.Button):
    pass


class Checkbutton(TextMixin, tk.Checkbutton):
    pass


class TtkCheckbutton(TextMixin, ttk.Checkbutton):
    pass


class Toplevel(tk.Toplevel):
    def title(self, string=None):
        if string is None:
            return super().title()
        self._source_title = string
        register(self)
        return super().title(display(self, string))

    def _apply_language(self):
        if hasattr(self, "_source_title"):
            super().title(display(self, self._source_title))


class Combobox(ttk.Combobox):
    """A display-only translation map over stable original filter values."""
    def __init__(self, master=None, **kwargs):
        self._source_values = tuple(kwargs.pop("values", ()))
        self._source_variable = kwargs.pop("textvariable", None)
        self._display_variable = tk.StringVar(master)
        self._syncing = False
        self._translate_choices = kwargs.get("state") == "readonly"
        super().__init__(master, textvariable=self._display_variable, **kwargs)
        self._source_trace = None
        if isinstance(self._source_variable, tk.Variable):
            self._source_trace = self._source_variable.trace_add("write", lambda *_: self._apply_language())
        self._display_trace = self._display_variable.trace_add("write", self._selected)
        self.bind("<Destroy>", self._release_traces, add="+")
        register(self)
        self._apply_language()

    def _choice(self, value):
        return display(self, value) if self._translate_choices else str(value)

    def _apply_language(self):
        if self._syncing:
            return
        self._syncing = True
        try:
            super().configure(values=tuple(self._choice(value) for value in self._source_values))
            value = self._source_variable.get() if isinstance(self._source_variable, tk.Variable) else ""
            source = next((item for item in self._source_values if str(item) == str(value)), value)
            self._display_variable.set(self._choice(source))
        finally:
            self._syncing = False

    def _selected(self, *_):
        if self._syncing or not isinstance(self._source_variable, tk.Variable):
            return
        value = self._display_variable.get()
        original = next((item for item in self._source_values if self._choice(item) == value), value)
        self._source_variable.set(str(original))

    def get(self):
        return str(self._source_variable.get()) if isinstance(self._source_variable, tk.Variable) else super().get()

    def set(self, value):
        if isinstance(self._source_variable, tk.Variable):
            self._source_variable.set(value)
        else:
            super().set(value)

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, str):
            return super().configure(cnf, **kwargs)
        options = dict(cnf or {}, **kwargs)
        changed = "values" in options
        if changed:
            self._source_values = tuple(options.pop("values"))
        result = super().configure(**options)
        if changed:
            self._apply_language()
        return result

    config = configure

    def _release_traces(self, event):
        if event.widget is not self:
            return
        if self._source_trace:
            self._source_variable.trace_remove("write", self._source_trace)
            self._source_trace = None
        self._display_variable.trace_remove("write", self._display_trace)


class Treeview(ttk.Treeview):
    def __init__(self, master=None, **kwargs):
        self._source_headings = {}
        self._source_rows = {}
        super().__init__(master, **kwargs)
        register(self)

    def heading(self, column, option=None, **kwargs):
        if "text" in kwargs:
            self._source_headings[column] = kwargs["text"]
            kwargs["text"] = display(self, kwargs["text"])
        return super().heading(column, option, **kwargs)

    def insert(self, parent, index, iid=None, **kwargs):
        source = kwargs.get("values", ())
        kwargs["values"] = tuple(display(self, item) for item in source)
        result = super().insert(parent, index, iid, **kwargs)
        self._source_rows[result] = source
        return result

    def delete(self, *items):
        for item in items:
            self._source_rows.pop(item, None)
        return super().delete(*items)

    def _apply_language(self):
        for column, source in self._source_headings.items():
            super().heading(column, text=display(self, source))
        for iid, values in self._source_rows.items():
            if self.exists(iid):
                super().item(iid, values=tuple(display(self, value) for value in values))
