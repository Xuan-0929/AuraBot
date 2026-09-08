/** Verifies wall-clock pose transitions and SAY identity ownership without mocking the scheduler.
 * Synthetic continuous curves isolate blend math; real Cubism/Core evaluation is covered by Electron.
 */
import { describe, expect, it } from 'vitest';
import { PoseChannel, type PoseClip } from '../../../src/features/live2d/poseChannel';
const clip=(value:number,durationMs=1000):PoseClip=>({durationMs,fadeInMs:250,fadeOutMs:200,sample:(out,time,base)=>{out.set(base);out[0]=value+time/10000;}});
describe('pose channel',()=>{
 it('has equal progression at 30,60,120 Hz',()=>{
  const poses=[30,60,120].map(hz=>{const p=new PoseChannel(new Float32Array([2,0.8]));p.setBaseline(clip(5));for(let i=0;i<hz;i++)p.advance(1000/hz);return Array.from(p.pose);});
  expect(poses[0]![0]).toBeCloseTo(poses[1]![0]!,5);expect(poses[1]![0]).toBeCloseTo(poses[2]![0]!,5);
 });
 it('enters from current pose and restores gesture-only parameters including opacity',()=>{
  const p=new PoseChannel(new Float32Array([2,0.8]));p.setBaseline(clip(5));p.advance(100);p.advance(100);p.advance(100);
  const before=Array.from(p.pose);const gesture={...clip(20,500),sample:(out:Float32Array)=>{out[0]=20;out[1]=0.1;}};
  p.play(gesture);expect(Array.from(p.pose)).toEqual(before);p.advance(1);expect(Math.abs(p.pose[0]!-before[0]!)).toBeLessThan(0.01);
  for(let i=0;i<10;i++)p.advance(100);expect(p.pose[0]).toBeGreaterThan(5);expect(p.pose[1]).toBeCloseTo(0.8);
 });
 it('does not restart baseline on duplicate state and bounds long or invalid frames',()=>{
  const p=new PoseChannel(new Float32Array([0]));const idle=clip(1);p.setBaseline(idle);p.advance(100);p.setBaseline(idle);p.advance(100);
  expect(p.elapsedMs).toBe(200);p.advance(NaN);p.advance(-20);expect(p.elapsedMs).toBe(200);p.advance(9000);expect(p.elapsedMs).toBe(300);
 });
 it('new gestures replace ownership and cancellation returns continuously',()=>{
  const p=new PoseChannel(new Float32Array([0]));p.play(clip(5));p.advance(100);p.play(clip(-5));const before=p.pose[0]!;p.advance(1);expect(Math.abs(p.pose[0]!-before)).toBeLessThan(0.01);
  p.cancel();for(let i=0;i<5;i++)p.advance(100);expect(p.pose[0]).toBe(0);
 });
});
