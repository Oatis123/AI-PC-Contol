import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from langchain_core.messages import HumanMessage

from agent.agent import request_to_agent_async, request_to_agent_sync
from datasama_client import DataSamaClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

datasama_client = DataSamaClient()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Start WebSocket connection loop to Data-Sama
    logging.info("Starting Data-Sama integration client...")
    await datasama_client.start()
    
    # Warm up OmniParser vision engine in background thread
    try:
        import asyncio
        from agent.vision.omniparser_engine import OmniParserEngine
        logging.info("Warming up OmniParser Vision Engine...")
        asyncio.get_event_loop().run_in_executor(None, OmniParserEngine)
    except Exception as e:
        logging.warning(f"Could not pre-load OmniParser engine: {e}")
        
    yield
    # Shutdown: Stop WebSocket connection
    logging.info("Stopping Data-Sama integration client...")
    await datasama_client.stop()

app = FastAPI(title="AI-PC-Control Server Mode", lifespan=lifespan)


class ToolInvocation(BaseModel):
    tool_name: str | None = "execute_pc_task"
    arguments: dict | None = None


class CommandRequest(BaseModel):
    commands: list[str]


async def _execute_pc_task_background(prompt_text: str):
    """Background task running the LangGraph agent and sending WebSocket updates."""
    try:
        logging.info(f"--- [TASK START] Processing prompt: '{prompt_text}' ---")
        
        # 1. Send single background state update over WS
        await datasama_client.send_background_update(f"Выполняется задача: {prompt_text}")

        # 2. Execute local PC control agent
        messages = [HumanMessage(content=prompt_text)]
        agent_response_messages = await request_to_agent_async(messages)

        final_content = ""
        if agent_response_messages:
            # Extract last meaningful AIMessage text content
            from langchain_core.messages import AIMessage
            for msg in reversed(agent_response_messages):
                if isinstance(msg, AIMessage) and msg.content and msg.content != "Вызываю инструменты...":
                    final_content = msg.content
                    break
            if not final_content and agent_response_messages:
                final_content = str(agent_response_messages[-1].content)

        if not final_content:
            final_content = "Задача выполнена (нет текстового ответа от агента)."

        logging.info(f"--- [TASK FINISHED] Sending tool_result to Data-Sama: '{final_content}' ---")

        # 3. Send final tool_result over WS
        await datasama_client.send_tool_result(
            tool_name="execute_pc_task",
            status="success",
            output=final_content
        )
        logging.info("--- [WS SENT] tool_result successfully sent to Data-Sama! ---")
    except Exception as e:
        logging.error(f"Error executing agent task '{prompt_text}': {e}", exc_info=True)
        await datasama_client.send_tool_result(
            tool_name="execute_pc_task",
            status="error",
            output=f"Ошибка при выполнении задачи на ПК: {str(e)}"
        )


@app.post("/tools/run-pc-agent")
async def run_pc_agent(payload: dict, background_tasks: BackgroundTasks):
    """
    Data-Sama async tool execution endpoint.
    Expects payload format from Data-Sama:
    {"tool_name": "execute_pc_task", "arguments": {"prompt": "..."}}
    """
    arguments = payload.get("arguments", {})
    if not isinstance(arguments, dict):
        arguments = {}

    prompt_text = arguments.get("prompt") or payload.get("prompt") or "Запусти проверку системы"

    # Dispatch background execution
    background_tasks.add_task(_execute_pc_task_background, prompt_text)

    # Immediately respond with 200 OK
    return "Задача принята в обработку агентом ПК."


@app.post("/run")
async def run_agent_command(data: CommandRequest):
    """Legacy endpoint for direct asynchronous command execution."""
    messages = [HumanMessage(content=c) for c in data.commands]
    agent_response_messages = await request_to_agent_async(messages)
    final_content = ""
    if agent_response_messages:
        final_content = str(agent_response_messages[-1].content)
    return {"response": final_content}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=5050)