from app.application.ports.agent_client import AgentClient


class StubAgentClient(AgentClient):
    async def run(self, agent_name: str, model_name: str, payload: dict) -> dict:
        if agent_name == "requirement-analysis-agent":
            raw = payload["raw_requirement"]
            categories = self._categories_for(raw)
            return {
                "model": model_name,
                "usage_purpose": "survey_or_research",
                "requested_data_sentence": f"Collect data required for: {raw}",
                "categories": categories,
                "delivery_channel": "API",
                "output_formats": ["csv", "visualization", "report"],
            }

        if agent_name == "data-selection-agent":
            return {
                "model": model_name,
                "selected_tables": [
                    {"table": "members_pseudonymized", "reason": "member attributes match categories"},
                    {"table": "merchant_profiles", "reason": "merchant context is needed"},
                    {"table": "transactions_pseudonymized", "reason": "payment behavior is needed"},
                ],
                "selection_query": {
                    "vector_similarity": True,
                    "top_k": 20,
                    "filters": payload.get("analysis", {}).get("categories", {}),
                },
            }

        if agent_name == "data-processing-agent":
            selected_data_summary = payload.get("selected_data_summary", {})
            processed_columns = selected_data_summary.get(
                "suggested_columns",
                [
                    "segment_id",
                    "gender",
                    "region",
                    "payment_tendency",
                    "transaction_count",
                    "avg_payment_amount",
                ],
            )
            return {
                "model": model_name,
                "processed_columns": processed_columns,
                "api_result": {
                    "items": [],
                    "meta": {
                        "generated_by": agent_name,
                        "source_row_count": selected_data_summary.get("row_count"),
                        "total_amount": selected_data_summary.get("total_amount"),
                    },
                },
                "csv_columns": processed_columns,
                "visualization": {
                    "chart_type": "bar",
                    "x": selected_data_summary.get("primary_dimension", "region"),
                    "y": selected_data_summary.get("primary_metric", "avg_payment_amount"),
                },
                "report": {
                    "title": "Requirement Based Data Processing Report",
                    "summary": selected_data_summary.get(
                        "summary",
                        "Selected data was transformed into API, CSV, visualization, and report outputs.",
                    ),
                },
            }

        return {"model": model_name, "agent_name": agent_name}

    def _categories_for(self, raw_requirement: str) -> dict:
        categories = {
            "gender": ["male", "female"],
            "region": ["capital_area", "non_capital_area"],
        }
        if any(keyword in raw_requirement for keyword in ["여행", "관광", "숙박"]):
            categories.update({"hobby": ["travel"], "payment_tendency": ["impulse"]})
        elif any(keyword in raw_requirement for keyword in ["카페", "커피", "디저트"]):
            categories.update({"merchant_category": ["cafe", "dessert"], "visit_time": ["morning", "afternoon"]})
        elif any(keyword in raw_requirement for keyword in ["육아", "키즈", "교육"]):
            categories.update({"life_stage": ["parenting"], "merchant_category": ["education", "kids"]})
        elif any(keyword in raw_requirement for keyword in ["헬스", "운동", "피트니스"]):
            categories.update({"interest": ["fitness"], "merchant_category": ["sports", "health"]})
        elif any(keyword in raw_requirement for keyword in ["구독", "정기결제", "OTT"]):
            categories.update({"payment_type": ["subscription"], "merchant_category": ["ott", "digital_content"]})
        else:
            categories.update({"interest": ["general"], "payment_tendency": ["unknown"]})
        return categories
