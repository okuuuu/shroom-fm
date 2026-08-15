from shroom_fm.wfs import layer_summary


class _FakeMeta:
    def __init__(self, title, abstract):
        self.title = title
        self.abstract = abstract


class _FakeWFS:
    def __init__(self, contents):
        self.contents = contents


def test_layer_summary_extracts_fields_and_sorts_by_name():
    wfs = _FakeWFS(
        {
            "metsaregister:eraldis_element": _FakeMeta(
                "Eraldis element", "Tree composition"
            ),
            "metsaregister:eraldis": _FakeMeta("Eraldis", "Stand geometry"),
        }
    )

    result = layer_summary(wfs)

    assert result == [
        {
            "name": "metsaregister:eraldis",
            "title": "Eraldis",
            "abstract": "Stand geometry",
        },
        {
            "name": "metsaregister:eraldis_element",
            "title": "Eraldis element",
            "abstract": "Tree composition",
        },
    ]
