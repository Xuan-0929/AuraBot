/** Checks real placement math and host-size fallbacks used by preview and Pet.
 * Full framing must contain the model canvas even after zoom/pan; portrait framing fits its width and keeps its top
 * visible while permitting a lower-body crop without changing the anchor convention.
 */
import { describe, expect, test } from "vitest";
import {
  calculateLive2DPlacement,
  resolveLive2DHostSize
} from "../../../src/features/live2d/live2dDiagnostics";

describe("resolveLive2DHostSize", () => {
  test("uses client dimensions when the host has already been laid out", () => {
    expect(resolveLive2DHostSize({ clientWidth: 240, clientHeight: 360 }, { width: 270, height: 380 })).toEqual({
      width: 240,
      height: 360
    });
  });

  test("falls back to the bounding rect before client dimensions are ready", () => {
    expect(
      resolveLive2DHostSize(
        {
          clientWidth: 0,
          clientHeight: 0,
          getBoundingClientRect: () => ({ width: 250.4, height: 370.2 })
        },
        { width: 270, height: 380 }
      )
    ).toEqual({
      width: 250,
      height: 370
    });
  });

  test("uses a non-zero fallback to avoid a blank first render", () => {
    expect(resolveLive2DHostSize({ clientWidth: 0, clientHeight: 0 }, { width: 270, height: 380 })).toEqual({
      width: 270,
      height: 380
    });
  });
});

describe("calculateLive2DPlacement", () => {
  test("keeps the visual model center in the host while preserving the lower body anchor", () => {
    expect(
      calculateLive2DPlacement({
        hostSize: { width: 270, height: 380 },
        modelSize: { width: 900, height: 1600 },
        compact: true
      })
    ).toEqual({
      x: 135,
      y: 361,
      scale: 0.2375
    });
  });

  test("applies a view scale multiplier so zooming changes the visible crop", () => {
    expect(
      calculateLive2DPlacement({
        hostSize: { width: 270, height: 380 },
        modelSize: { width: 900, height: 1600 },
        compact: true,
        viewTransform: { scale: 0.5, offset: { x: 12, y: -8 } }
      })
    ).toEqual({
      x: 147,
      y: 272.63,
      scale: 0.125875
    });
  });
});

test.each([{width:900,height:1600},{width:1600,height:900}])('contains the full model after extreme zoom and pan: %o',modelSize=>{
 const hostSize={width:270,height:380};
 for(const scale of [0.35,1,2.4])for(const direction of [-1,1]){
  const p=calculateLive2DPlacement({hostSize,modelSize,compact:false,showFullModel:true,viewTransform:{scale,offset:{x:direction*1000,y:direction*1000}}});
  expect(p.x-modelSize.width*p.scale*0.5).toBeGreaterThanOrEqual(-0.0001);expect(p.x+modelSize.width*p.scale*0.5).toBeLessThanOrEqual(hostSize.width+0.0001);
  expect(p.y-modelSize.height*p.scale*0.95).toBeGreaterThanOrEqual(-0.0001);expect(p.y+modelSize.height*p.scale*0.05).toBeLessThanOrEqual(hostSize.height+0.0001);
 }
});
test('portrait framing enlarges the upper portion while full framing keeps the existing default fit',()=>{
 const args={hostSize:{width:270,height:380},modelSize:{width:900,height:1600},compact:false};
 const full=calculateLive2DPlacement(args),portrait=calculateLive2DPlacement({...args,showFullModel:false});
 expect(full.scale).toBeCloseTo(0.2185);expect(portrait.scale).toBeGreaterThan(full.scale);
 expect(portrait.y-1600*portrait.scale*0.95).toBeCloseTo(0);expect(portrait.y+1600*portrait.scale*0.05).toBeGreaterThan(380);
});

test.each([{width:900,height:1600},{width:1600,height:900}])('portrait fits width and aligns top regardless of host height: %o',modelSize=>{
 for(const height of [200,600]){
  const p=calculateLive2DPlacement({hostSize:{width:300,height},modelSize,compact:false,showFullModel:false});
  expect(modelSize.width*p.scale).toBeCloseTo(300);expect(p.x-modelSize.width*p.scale*0.5).toBeCloseTo(0);
  expect(p.y-modelSize.height*p.scale*0.95).toBeCloseTo(0);
 }
});
