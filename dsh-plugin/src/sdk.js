import { assertObjectJsonSchema, validateJsonSchemaValue } from "@deepseek-ai/dsh-tools";
//#region packages/runtime-sdk/src/index.ts
/** Register once on plugin enable. DSH still owns policy, guards and dispatch. */
function registerRunTool(ctx, definition) {
	const runtime = ctx.get("sfRuntime");
	if (!runtime) throw new Error("Runtime尚未启用；业务插件需要 inject: [\"tools\", \"sfRuntime\"]");
	assertObjectJsonSchema(definition.inputSchema);
	const parameters = structuredClone(definition.inputSchema);
	const tool = {
		name: definition.toolId,
		description: definition.description,
		parameters,
		output: {
			schema: {
				type: "object",
				properties: {
					text: { type: "string" },
					files: {
						type: "array",
						items: { type: "string" }
					}
				},
				additionalProperties: false
			},
			render: (_args, value) => [{
				type: "text",
				text: JSON.stringify(value)
			}]
		},
		async execute(input, exec) {
			const execution = runtime.executionFor(exec, tool);
			const errors = validateJsonSchemaValue(parameters, input, "input");
			if (errors.length) throw new Error("INPUT_VALIDATION_FAILED: " + errors.join("; "));
			const compatible = execution.interaction ? execution : Object.freeze({
				...execution,
				interaction: Object.freeze({ ask: async () => {
					throw new Error("INTERACTION_UNSUPPORTED: 人工交互需要 Runtime 0.4.0 或以上");
				} })
			});
			return definition.execute(input, compatible);
		}
	};
	return ctx.effect(() => {
		const removeTool = ctx.tools.register(tool);
		let removeEntry;
		try {
			removeEntry = runtime.registerEntry(tool, definition.modelProtocols ? { modelProtocols: definition.modelProtocols } : void 0);
		} catch (error) {
			removeTool();
			throw error;
		}
		return () => {
			removeEntry();
			removeTool();
		};
	}, "software-factory: run tool " + definition.toolId);
}
//#endregion
export { registerRunTool };
