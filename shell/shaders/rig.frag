#version 440
// Bends her flat artwork a little, the way a Live2D rig would: the head tilts, nods and
// turns over the neck, the chest rises with each breath and the hair tips sway.
// Every offset is a smooth field and the image is sampled backwards, so nothing tears.
// Rebuild after editing: /usr/lib/qt6/bin/qsb --qt6 -o rig.frag.qsb rig.frag

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;

layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    vec2 rigSize;     // the layer, in pixels
    vec2 neck;        // where the head pivots
    vec2 faceCentre;
    vec2 faceRadius;
    float headAngle;  // radians, positive leans her head to her left (clockwise on screen)
    float headTurn;   // pixels her features shift sideways
    float headNod;    // pixels her head drops, negative lifts the chin
    float breath;     // pixels her chest rises
    float sway;       // pixels her hair tips swing sideways
};

layout(binding = 1) uniform sampler2D source;

void main() {
    vec2 p = qt_TexCoord0 * rigSize;
    vec2 q = p;

    // Breathing lifts everything above the waist, fading out toward the bottom.
    q.y += breath * smoothstep(rigSize.y, neck.y + 40.0, p.y);

    // The head turns as one piece above the neck, blending into the shoulders.
    float head = 1.0 - smoothstep(neck.y - 12.0, neck.y + 36.0, p.y);
    float a = -headAngle * head;
    vec2 d = q - neck;
    q = neck + vec2(cos(a) * d.x - sin(a) * d.y, sin(a) * d.x + cos(a) * d.y);
    q.y -= headNod * head;

    // Turning: features in the middle of the face shift more than its outline.
    vec2 f = (q - faceCentre) / faceRadius;
    float bulge = max(0.0, 1.0 - dot(f, f));
    q.x -= headTurn * bulge * bulge;

    // Hair tips: below the shoulders, more toward the bottom and the outer edges.
    float low = smoothstep(neck.y + 20.0, rigSize.y, p.y);
    float side = smoothstep(0.22, 0.45, abs(p.x / rigSize.x - 0.5));
    q.x -= sway * low * side;

    fragColor = texture(source, q / rigSize) * qt_Opacity;
}
