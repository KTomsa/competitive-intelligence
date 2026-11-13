"""Offline session fake for source adapter tests; not a SQL Server emulator.

It evaluates the adapter's simple SELECT predicates over shared rows. Actual
constraints, locking and driver behavior require the opt-in Azure tests.
"""

from collections import defaultdict
from contextlib import contextmanager
from copy import deepcopy
from threading import RLock

from sqlalchemy.sql import operators
from sqlalchemy.sql.elements import BindParameter, False_, True_, UnaryExpression


def matches(row, clause):
    if hasattr(clause, "clauses"):
        return all(matches(row, part) for part in clause.clauses)
    right = clause.right
    value = right.value if isinstance(right, BindParameter) else isinstance(right, True_)
    assert isinstance(right, (BindParameter, True_, False_))
    left = getattr(row, clause.left.key)
    if clause.operator is operators.is_:
        return left is value
    return clause.operator(left, value)


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)


class SourceSessions:
    def __init__(self):
        self.rows = defaultdict(list)
        self.statements = []
        self.lock = RLock()

    @contextmanager
    def __call__(self):
        with self.lock:
            yield SourceSession(self)

    @contextmanager
    def begin(self):
        with self.lock:
            before = deepcopy(self.rows)
            try:
                yield SourceSession(self)
            except Exception:
                self.rows = before
                raise


class SourceSession:
    def __init__(self, database):
        self.database = database

    def add(self, row):
        self.database.rows[type(row)].append(row)

    def flush(self):
        pass

    def get(self, model, key):
        name = list(model.__table__.primary_key)[0].key
        return next((row for row in self.database.rows[model] if getattr(row, name) == key), None)

    def scalars(self, statement):
        self.database.statements.append(statement)
        model = statement.column_descriptions[0]["entity"]
        rows = [row for row in self.database.rows[model]
                if statement.whereclause is None or matches(row, statement.whereclause)]
        for order in reversed(statement._order_by_clauses):
            column = order.element if isinstance(order, UnaryExpression) else order
            rows.sort(key=lambda row: getattr(row, column.key),
                      reverse=isinstance(order, UnaryExpression) and order.modifier is operators.desc_op)
        if statement._limit_clause is not None:
            rows = rows[:statement._limit_clause.value]
        return Rows(rows)

    def scalar(self, statement):
        rows = self.scalars(statement).all()
        return rows[0] if rows else None
