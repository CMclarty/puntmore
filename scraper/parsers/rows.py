"""Rows with a line to say: what a parser hands the runner when there is
something about the page worth a line in the job's log (a page still
loading, a count that does not add up). A plain list everywhere else
(json, the sinks); the runner reads `said` with getattr, so a parser that
returns a bare list says nothing."""


class Rows(list):
    def __init__(self, rows=(), said: str | None = None):
        super().__init__(rows)
        self.said = said
