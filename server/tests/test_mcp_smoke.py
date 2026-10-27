READ_TOOLS = {"ping", "list_docs", "get_doc", "count_docs", "get_doctype_schema", "run_report", "get_stock_levels"}


async def test_tools_listed():
    from erpnext_mcp.tools import mcp

    names = {t.name for t in await mcp.list_tools()}
    assert READ_TOOLS <= names
