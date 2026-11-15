import { DoubleSide, ShaderMaterial, Vector3 } from 'three'
import type { Color } from 'three'

// The desktop's per-pixel shader (viewport.py _FRAG) in view space: key light near the viewer from the right, a soft
// top light, a faint highlight and rim. The camera is orthographic, so the view vector is always (0, 0, 1).
const vertex = /* glsl */ `
varying vec3 vN;
void main() {
  vN = normalize(normalMatrix * normal);
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}
`

const fragment = /* glsl */ `
uniform vec3 baseColor;
uniform float alpha;
uniform float shine;
varying vec3 vN;
void main() {
  vec3 n = normalize(vN);
  vec3 v = vec3(0.0, 0.0, 1.0);
  if (dot(n, v) < 0.0) n = -n;
  vec3 L1 = normalize(vec3(0.55, 0.12, 1.0));
  vec3 L2 = normalize(vec3(-0.6, 0.8, 0.3));
  float d1 = max(dot(n, L1), 0.0);
  float d2 = max(dot(n, L2), 0.0);
  vec3 col = baseColor * (0.50 + 0.52 * d1 + 0.06 * d2);
  float s1 = pow(max(dot(n, normalize(L1 + v)), 0.0), 18.0);
  float s2 = pow(max(dot(n, normalize(L2 + v)), 0.0), 18.0);
  float rim = pow(1.0 - max(dot(n, v), 0.0), 4.0);
  col += shine * (0.36 * s1 + 0.08 * s2) * vec3(0.90, 0.96, 1.0) + shine * 0.05 * rim;
  gl_FragColor = vec4(min(col, vec3(1.0)), alpha);
}
`

export interface ShadedOptions {
  /** 1 for a solid, less for a Context body (drawn blended, without depth writes). */
  alpha?: number
}

/**
 * A shaded face material. Faces are pushed back in depth (polygon offset) so the edge lines drawn at the same depth
 * win the depth test and do not flicker against the faces.
 */
export function shadedMaterial(base: Color, { alpha = 1 }: ShadedOptions = {}): ShaderMaterial {
  const m = new ShaderMaterial({
    uniforms: {
      baseColor: { value: new Vector3(base.r, base.g, base.b) },
      alpha: { value: alpha },
      shine: { value: 1 },
    },
    vertexShader: vertex,
    fragmentShader: fragment,
    side: DoubleSide,
    transparent: alpha < 1,
    depthWrite: alpha >= 1,
    polygonOffset: true,
    polygonOffsetFactor: 1,
    polygonOffsetUnits: 1,
  })
  return m
}
