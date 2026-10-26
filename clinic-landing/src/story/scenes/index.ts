import type { SceneBuilder } from "../rig";
import hook from "./hook";
import intro from "./intro";
import gap from "./gap";
import plan from "./plan";
import measure from "./measure";
import alert from "./alert";
import calendar from "./calendar";
import result from "./result";
import cta from "./cta";

/** شناسهٔ هر صحنه باید با id در narration.json یکی باشد */
export const SCENES: Record<string, SceneBuilder> = { hook, intro, gap, plan, measure, alert, calendar, result, cta };
