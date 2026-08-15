"""Short educational text kept out of the Streamlit controller."""

WAVEFORM = """
The waveform shows air-pressure variation over time. Vowels often look periodic; closures and pauses
approach zero; bursts appear as brief transients. Amplitude depends heavily on recording gain and distance.
"""

SPECTRUM = """
A spectrum shows which frequencies are present in the selected time region. Peaks may be the fundamental
frequency, its harmonics, vocal-tract resonances, or noise depending on the sound. Long selections are
summarized with averaged short-time power because one FFT does not cleanly describe a changing utterance.
"""

LINEAR_SPECTROGRAM = """
Time runs left to right, frequency bottom to top, and color represents level in dB. Harmonics form fine
horizontal stripes in voiced speech; broader dark bands often reflect resonances; fricatives create diffuse
high-frequency energy; stops may show closure followed by a burst.
"""

MEL_SPECTROGRAM = """
The Mel representation compresses the frequency axis to devote more resolution to lower frequencies. It is
common as machine-learning input, but it is less physically direct than a linear-frequency spectrogram.
"""

FORMANTS = """
F1-F3 are rough LPC estimates, not measurements. They are most useful in steady voiced vowels and can fail
for high-pitched voices, consonants, noise, short windows, or poor recordings.
"""

COMPARISON_WARNING = """
Visible differences can result from speaker anatomy, pitch, loudness, microphone, room, timing, and spoken
content—not only accent. This view does not align recordings and makes no accent judgment.
"""

