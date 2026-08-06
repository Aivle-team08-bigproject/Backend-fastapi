import asyncio

from agent_runtime.query.metadata import load_dataset_metadata


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _Session:
    async def execute(self, _statement):
        return _Result(
            [
                {
                    "table_name": "merchants",
                    "table_comment": "익명 가맹점",
                    "column_name": "merchant_region",
                    "data_type": "character varying",
                    "column_comment": "가맹점 지역",
                },
                {
                    "table_name": "customers",
                    "table_comment": "익명 고객",
                    "column_name": "age_band",
                    "data_type": "character varying",
                    "column_comment": "연령대",
                },
                {
                    "table_name": "transactions",
                    "table_comment": "익명 거래",
                    "column_name": "approval_status",
                    "data_type": "character varying",
                    "column_comment": "승인 상태",
                },
            ]
        )


def test_load_dataset_metadata_canonicalizes_supervisor_aliases():
    metadata = asyncio.run(
        load_dataset_metadata(
            _Session(),
            ["merchant", "member_pseudonymized", "transaction_pseudonymized"],
        )
    )

    assert [item["dataset"] for item in metadata] == [
        "anon_merchants",
        "anon_customers",
        "anon_transactions",
    ]
    assert [item["table"] for item in metadata] == [
        "merchants",
        "customers",
        "transactions",
    ]
